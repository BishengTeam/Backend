from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.domain.order.src.index import Order, apply_order_status_transition
from app.domain.plan.src.index import Plan
from app.domain.renshe.src.index import (
    EDUCATION_LEVELS,
    RENSHE_ORDER_EXPIRE_MINUTES,
    RensheApplication,
    RensheApplicationVersion,
    RensheAuditLog,
    RensheMaterial,
    RensheRefundRequest,
    RensheReview,
    assert_application_transition,
)
from app.models.cert_product import CertProduct
from app.domain.user.src.index import User, UserRealname, UserStudent
from app.integrations.renshe_storage import RensheObjectStorage
from app.port.exceptions import BusinessException, ConflictException, NotFoundException
from app.schemas.common import PaginatedData
from app.schemas.renshe import (
    RensheApplicationDetailResponse,
    RensheApplicationListItem,
    RensheApplicationOrderSummary,
    RensheApplicationPlanSummary,
    RensheApplicationRefundSummary,
    RensheApplicationResponse,
    RensheApplicationStatus,
    RensheApplicationSubmitResponse,
    RensheDraftUpsert,
    RensheVersionSummary,
)
from app.services.agreement_template import ensure_accepted
from app.services.clock import db_now_utc
from app.services.consultation_group import ConsultationGroupService
from app.services.plan_enrollment import CAPACITY_OCCUPYING_ORDER_STATUSES, PlanEnrollmentService
from app.services.renshe_project import batch_configuration, is_renshe_plan, default_configuration
from app.services.renshe_batch_project import (
    active_applications_for_project,
    active_applications_for_plan,
    resolve_project,
    resolve_application_project,
    resolve_enrollable_project,
    select_write_application,
)
from app.services.renshe_graduated import is_self_reported, validate_admission
from app.services.renshe_reporting import resolve_contract, validate_reporting, validate_snapshot, is_v2
from app.utils.payment import generate_out_trade_no
from app.utils.pii import identity_hash


logger = logging.getLogger(__name__)
EDITABLE_APPLICATION_STATUSES = {"draft", "initial_rejected", "external_rejected"}


class RensheApplicationService:
    def __init__(self, storage: RensheObjectStorage | None = None) -> None:
        self.storage = storage or RensheObjectStorage()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _assert_supported_program(plan: Plan, config: dict | None = None) -> dict:
        return config if config is not None else batch_configuration(plan)

    @staticmethod
    async def _project_config(db, plan: Plan, resolution) -> dict:
        if resolution.config_id is not None:
            from app.models.renshe_project import RensheProjectConfig

            row = await db.get(RensheProjectConfig, resolution.config_id)
            if row is None:
                raise ConflictException("报考项目配置版本不存在，请联系管理员")
            return row.config
        return batch_configuration(plan)

    async def save_draft(
        self, user_id: int, data: RensheDraftUpsert
    ) -> RensheApplicationResponse:
        async with get_db_ctx() as db:
            await self._lock_active_user(db, user_id)
            plan = await db.scalar(
                select(Plan).where(Plan.id == data.plan_id).with_for_update()
            )
            if not await is_renshe_plan(db, plan):
                raise NotFoundException("人社报名批次")
            explicit_project_id = data.batch_project_id
            if explicit_project_id is not None:
                # 同批次不同项目互不干扰：只在该项目维度内查活跃报名。
                active_applications = await active_applications_for_project(
                    db,
                    user_id=user_id,
                    plan_id=data.plan_id,
                    batch_project_id=explicit_project_id,
                )
                resolved_project = (
                    await resolve_project(
                        db, plan, batch_project_id=explicit_project_id
                    )
                    if active_applications
                    else await resolve_enrollable_project(
                        db, plan, explicit_project_id
                    )
                )
            else:
                # 未显式传项目：优先沿用 legacy NULL 报名，仅在可唯一推断项目时收养。
                active_applications = await active_applications_for_project(
                    db,
                    user_id=user_id,
                    plan_id=data.plan_id,
                    batch_project_id=None,
                )
                if active_applications:
                    try:
                        resolved_project = await resolve_enrollable_project(
                            db, plan, None
                        )
                    except BusinessException:
                        # 项目已停用/不可报名时仍允许历史补正：若用户仅有
                        # 一条项目维度报名，就沿用该报名所属项目。
                        historical = await active_applications_for_plan(
                            db, user_id=user_id, plan_id=data.plan_id
                        )
                        if (
                            len(historical) == 1
                            and historical[0].batch_project_id is not None
                        ):
                            active_applications = historical
                            resolved_project = await resolve_project(
                                db,
                                plan,
                                batch_project_id=historical[0].batch_project_id,
                            )
                        else:
                            resolved_project = await resolve_project(
                                db, plan, batch_project_id=None
                            )
                    else:
                        if resolved_project.batch_project_id is not None:
                            active_applications = [
                                *active_applications,
                                *await active_applications_for_project(
                                    db,
                                    user_id=user_id,
                                    plan_id=data.plan_id,
                                    batch_project_id=resolved_project.batch_project_id,
                                ),
                            ]
                else:
                    try:
                        resolved_project = await resolve_enrollable_project(
                            db, plan, None
                        )
                    except BusinessException:
                        historical = await active_applications_for_plan(
                            db, user_id=user_id, plan_id=data.plan_id
                        )
                        if (
                            len(historical) == 1
                            and historical[0].batch_project_id is not None
                        ):
                            active_applications = historical
                            resolved_project = await resolve_project(
                                db,
                                plan,
                                batch_project_id=historical[0].batch_project_id,
                            )
                        else:
                            raise
                    else:
                        if resolved_project.batch_project_id is not None:
                            active_applications = await active_applications_for_project(
                                db,
                                user_id=user_id,
                                plan_id=data.plan_id,
                                batch_project_id=resolved_project.batch_project_id,
                                include_legacy=True,
                            )
            if len(active_applications) > 1:
                raise ConflictException(
                    "该项目存在多条活跃报名，请联系管理员整理后再报名"
                )
            existing_application = (
                active_applications[0] if active_applications else None
            )
            application, adopt_legacy = select_write_application(
                active_applications,
                explicit_project_id=data.batch_project_id,
                resolved_project_id=resolved_project.batch_project_id,
            )
            if adopt_legacy:
                application.batch_project_id = resolved_project.batch_project_id
            config = await self._project_config(db, plan, resolved_project)
            self._assert_supported_program(plan, config)
            if resolved_project.config_id is None and plan.renshe_config_id is None:
                raise ConflictException("批次尚未确认项目配置，请联系管理员核实历史归属")
            if application is None and not is_self_reported(config):
                raise ConflictException("本期新报名仅开放已适配批次；历史报名请从我的报名继续办理")
            if application is None and not is_v2(config.get("reporting_policy", {})):
                raise ConflictException("历史批次仅办理已有报名，新报名请选择当前批次")
            now = await db_now_utc(db)
            self._validate_edit_window(
                plan,
                application,
                paid_order=await self._paid_correction_order(db, application),
                now=now,
                config=config,
            )
            if application is None:
                application = RensheApplication(
                    user_id=user_id,
                    plan_id=data.plan_id,
                    batch_project_id=resolved_project.batch_project_id,
                    status="draft",
                )
                db.add(application)
                await db.flush()
            if application.status not in EDITABLE_APPLICATION_STATUSES:
                raise ConflictException("当前报名状态不可编辑")
            if application.frozen_at is not None:
                raise ConflictException("退款处理中，报名已冻结")
            realname, _ = await self._verified_profiles(db, user_id, plan, config)

            values = data.model_dump(mode="json", exclude_none=True)
            if (application.draft_data or {}).get("enrollment_notice"):
                values["enrollment_notice"] = application.draft_data["enrollment_notice"]
            if is_self_reported(config):
                contract = await resolve_contract(db, plan, resolved_project.config_id)
                values.update(await self._admission_values(db, application, realname, config, values))
                values["reporting_info"] = validate_reporting(values.get("reporting_info"), contract["config"]["reporting_policy"], config=config)
                values.pop("reporting_config_id", None)
                values["commitment_template"] = contract["config"]["reporting_policy"]["commitment_template"]
                values["materials"] = (application.draft_data or {}).get("materials", {})
            else:
                values["reporting_info"] = validate_reporting(values.get("reporting_info"), {}, config=config)
            application.draft_data = values
            db.add(
                self._audit(
                    user_id,
                    "application.save_draft",
                    application,
                    {"plan_id": application.plan_id},
                )
            )
            await db.commit()
            await db.refresh(application)
            response = RensheApplicationResponse.model_validate(application)
            if existing_application is not None:
                response = response.model_copy(update={"reused": True})
            return response

    async def submit(
        self, user_id: int, application_id: int, *, notice_hash: str | None = None, require_notice: bool = False
    ) -> RensheApplicationSubmitResponse:
        copied_keys: list[str] = []
        committed = False
        try:
            async with get_db_ctx() as db:
                application, plan, paid_order = await self._lock_submission_context(
                    db, user_id=user_id, application_id=application_id
                )
                if application.status not in EDITABLE_APPLICATION_STATUSES:
                    raise ConflictException("当前报名状态不可提交")
                resolution = await resolve_application_project(db, application, plan)
                config = await self._project_config(db, plan, resolution)
                self._assert_supported_program(plan, config)
                if resolution.config_id is None and plan.renshe_config_id is None:
                    raise ConflictException("批次尚未确认项目配置，请联系管理员核实历史归属")
                if application.frozen_at is not None:
                    raise ConflictException("退款处理中，报名已冻结")
                if not application.draft_data:
                    raise BusinessException("请先保存完整报名草稿")
                await ensure_accepted(
                    db,
                    user_id,
                    "cert_registration",
                    message="请先阅读并同意认证报名信息处理授权协议",
                )

                db_now = await db_now_utc(db)
                self._validate_edit_window(
                    plan, application, paid_order=paid_order, now=db_now, config=config
                )
                realname, student = await self._verified_profiles(db, user_id, plan, config)
                graduated = is_self_reported(config)
                now = db_now
                await validate_snapshot(db, plan, application.draft_data, config=config)
                if graduated:
                    try:
                        RensheDraftUpsert.model_validate({key: value for key, value in application.draft_data.items() if key not in {"materials", "reporting_contract", "commitment_template", "eligibility_snapshot", "enrollment_notice"}})
                    except ValueError as exc:
                        raise BusinessException("请先完整填写本申报路径的资格信息和联系方式并保存") from exc
                    policy = await validate_snapshot(db, plan, application.draft_data, config=config)
                    validate_reporting(application.draft_data.get("reporting_info"), policy, config=config)
                    application.draft_data = {**application.draft_data, **await self._admission_values(db, application, realname, config, application.draft_data, submitted_at=now)}

                if application.status != "draft" and paid_order is None:
                    raise ConflictException("驳回重提缺少原已支付订单")

                if paid_order is None:
                    await self._ensure_capacity(db, plan)
                    if require_notice and not notice_hash:
                        raise BusinessException("请先选择科目并阅读确认报名须知")
                    if notice_hash:
                        from app.services.certification_journey import confirmed_notice
                        application.draft_data = {**application.draft_data, "enrollment_notice": await confirmed_notice(db, vendor="renshe", batch_id=plan.id, notice_hash=notice_hash, batch_project_id=resolution.batch_project_id)}

                latest_no = await db.scalar(
                    select(func.max(RensheApplicationVersion.version_no)).where(
                        RensheApplicationVersion.application_id == application.id
                    )
                )
                version_no = (latest_no or 0) + 1
                version = RensheApplicationVersion(
                    application_id=application.id,
                    version_no=version_no,
                    project_config_id=resolution.config_id,
                    submitted_by_user_id=user_id,
                    realname_snapshot=self._realname_snapshot(realname),
                    student_snapshot={} if graduated else self._student_snapshot(student),
                    form_data=dict(application.draft_data),
                    batch_snapshot={
                        "exam_date": plan.exam_date.isoformat() if plan.exam_date else None,
                        "exam_location": plan.exam_location,
                        "exam_org_name": plan.exam_org_name,
                        "exam_org_code": plan.exam_org_code,
                        "exam_date_note": plan.exam_date_note,
                    },
                    submitted_at=now,
                )
                db.add(version)
                await db.flush()

                source_materials = ({
                    "id_card_front": realname.id_card_front_oss,
                    "id_card_back": realname.id_card_back_oss,
                    "portrait": realname.avatar_oss,
                    **{kind: metadata["storage_key"] for kind, metadata in application.draft_data.get("materials", {}).items()},
                } if graduated else self._material_sources(realname, student))
                from app.services.renshe_level_three import material_requirements
                for requirement in material_requirements(config, application.draft_data):
                    kind = requirement["kind"]
                    if not source_materials.get(kind) and not requirement["required"]:
                        continue
                    if not source_materials.get(kind):
                        raise BusinessException(f"请上传{requirement['name']}")
                    copied = await self.storage.copy_version_material(
                        user_id=user_id,
                        plan_id=plan.id,
                        application_id=application.id,
                        version_no=version_no,
                        kind=kind,
                        source_key=source_materials[kind],
                    )
                    copied_keys.append(copied.storage_key)
                    metadata = application.draft_data.get("materials", {}).get(kind) if graduated else None
                    if metadata and (copied.sha256 != metadata.get("sha256") or copied.size_bytes != metadata.get("size_bytes")):
                        raise ConflictException(f"{requirement['name']}内容已变化，请重新上传后提交")
                    if copied.extension not in requirement["extensions"]:
                        raise BusinessException(f"{requirement['name']}文件格式不符合本批次要求")
                    db.add(
                        RensheMaterial(
                            application_id=application.id,
                            version_id=version.id,
                            kind=copied.kind,
                            source_storage_key=copied.source_storage_key,
                            storage_key=copied.storage_key,
                            original_filename=(application.draft_data.get("materials", {}).get(kind, {}).get("original_filename") or copied.original_filename),
                            extension=copied.extension,
                            content_type=copied.content_type,
                            size_bytes=copied.size_bytes,
                            sha256=copied.sha256,
                        )
                    )

                application.current_version_id = version.id
                application.submitted_at = now
                application.correction_due_at = None

                if paid_order is not None:
                    assert_application_transition(application.status, "pending_initial_review")
                    application.status = "pending_initial_review"
                    order = paid_order
                    payment_required = False
                else:
                    assert_application_transition(application.status, "pending_payment")
                    application.status = "pending_payment"
                    draft = application.draft_data
                    order = Order(
                        user_id=user_id,
                        order_kind="certification",
                        product_type=resolution.product_code or plan.product_type,
                        plan_id=plan.id,
                        application_id=application.id,
                        candidate_name=realname.real_name,
                        candidate_phone=draft["contact_phone"],
                        candidate_idcard=realname.id_card_number,
                        price=resolution.price_cents,
                        status="pending",
                        out_trade_no=generate_out_trade_no("RS"),
                        expires_at=now + timedelta(minutes=RENSHE_ORDER_EXPIRE_MINUTES),
                        extra_data={
                            "application_version_id": version.id,
                            "business_type": "renshe",
                            "project_name": plan.project_name or resolution.occupation_name,
                            "project_config_id": resolution.config_id,
                            "batch_project_id": resolution.batch_project_id,
                        },
                    )
                    db.add(order)
                    await db.flush()
                    from app.services.offline_payment import OfflinePaymentService
                    await OfflinePaymentService.initialize(db, order)
                    payment_required = True

                db.add(
                    self._audit(
                        user_id,
                        "application.submit",
                        application,
                        {
                            "version_no": version_no,
                            "payment_required": payment_required,
                        },
                    )
                )
                await db.commit()
                committed = True
                await db.refresh(application)
                await db.refresh(order)
                return RensheApplicationSubmitResponse(
                    application=RensheApplicationResponse.model_validate(application),
                    version=RensheVersionSummary.model_validate(version),
                    order_id=order.id,
                    order_status=order.status,
                    order_expires_at=order.expires_at,
                    payment_required=payment_required,
                )
        except Exception:
            if copied_keys and not committed:
                try:
                    await self.storage.delete_many(copied_keys)
                except Exception as exc:
                    logger.error(
                        "failed to remove orphaned renshe version materials: exception_type=%s",
                        type(exc).__name__,
                    )
            raise

    async def _lock_submission_context(
        self, db, *, user_id: int, application_id: int
    ) -> tuple[RensheApplication, Plan, Order | None]:
        plan_id = await db.scalar(
            select(RensheApplication.plan_id).where(
                RensheApplication.id == application_id,
                RensheApplication.user_id == user_id,
            )
        )
        if plan_id is None:
            raise NotFoundException("人社报名")

        await self._lock_active_user(db, user_id)
        plan = await db.scalar(select(Plan).where(Plan.id == plan_id).with_for_update())
        if not await is_renshe_plan(db, plan):
            raise NotFoundException("人社报名批次")

        orders = (
            await db.execute(
                select(Order)
                .where(Order.application_id == application_id)
                .order_by(Order.id)
                .with_for_update()
            )
        ).scalars().all()
        application = await db.scalar(
            select(RensheApplication)
            .where(
                RensheApplication.id == application_id,
                RensheApplication.user_id == user_id,
            )
            .with_for_update()
        )
        if application is None:
            raise NotFoundException("人社报名")
        if application.plan_id != plan.id:
            raise ConflictException("报名归属批次已变化，请重试")
        multi = plan.product_type is None
        if any(
            order.plan_id != plan.id
            or order.user_id != user_id
            or (
                not multi
                and order.product_type != plan.product_type
            )
            or (
                multi
                and order.extra_data
                and order.extra_data.get("batch_project_id") not in {None, application.batch_project_id}
            )
            for order in orders
        ):
            raise ConflictException("报名与订单的项目、批次或用户归属不一致")

        paid_order = next(
            (
                order
                for order in reversed(orders)
                if order.status in {"paid", "completed"}
            ),
            None,
        )
        return application, plan, paid_order

    @classmethod
    def _validate_edit_window(
        cls, plan, application, *, paid_order=None, now=None, config: dict | None = None
    ):
        if config is None:
            config = batch_configuration(plan)
        if (is_self_reported(config) and application is not None
                and application.status in {"initial_rejected", "external_rejected"}):
            if plan.status not in {"published", "registration_closed"} or application.closed_at is not None:
                raise ConflictException("批次或报名已关闭，不能补正")
            deadline = application.correction_due_at
            current = now or cls._now()
            if deadline is None or PlanEnrollmentService._as_utc(deadline) < current:
                raise ConflictException("补正期限已过或尚未设置，请联系管理员")
            if paid_order is None:
                raise ConflictException("补正缺少原已支付订单，请联系管理员")
            return
        PlanEnrollmentService.validate_application_window(plan, now=now)

    @staticmethod
    async def _paid_correction_order(db, application):
        if application is None or application.status not in {"initial_rejected", "external_rejected"}:
            return None
        return await db.scalar(select(Order).where(
            Order.application_id == application.id, Order.plan_id == application.plan_id,
            Order.user_id == application.user_id, Order.status.in_(("paid", "completed"))).limit(1))

    async def upload_application_material(
        self,
        user_id: int,
        plan_id: int,
        kind: str,
        *,
        filename: str,
        content_type: str | None,
        content: bytes,
        batch_project_id: int | None = None,
    ) -> dict:
        from app.services.renshe_source import delete_unreferenced_source_keys
        stored = None
        old_key = None
        committed = False
        try:
            async with get_db_ctx() as db:
                await self._lock_active_user(db, user_id)
                plan = await db.scalar(select(Plan).where(Plan.id == plan_id).with_for_update())
                if not await is_renshe_plan(db, plan):
                    raise NotFoundException("人社批次")
                explicit_project_id = batch_project_id
                if explicit_project_id is not None:
                    applications = await active_applications_for_project(
                        db,
                        user_id=user_id,
                        plan_id=plan_id,
                        batch_project_id=explicit_project_id,
                    )
                    resolved_project = (
                        await resolve_project(
                            db,
                            plan,
                            batch_project_id=applications[0].batch_project_id,
                        )
                        if applications
                        else await resolve_enrollable_project(
                            db, plan, explicit_project_id
                        )
                    )
                else:
                    applications = await active_applications_for_project(
                        db,
                        user_id=user_id,
                        plan_id=plan_id,
                        batch_project_id=None,
                    )
                    if applications:
                        try:
                            resolved_project = await resolve_enrollable_project(
                                db, plan, None
                            )
                        except BusinessException:
                            historical = await active_applications_for_plan(
                                db, user_id=user_id, plan_id=plan_id
                            )
                            if (
                                len(historical) == 1
                                and historical[0].batch_project_id is not None
                            ):
                                applications = historical
                                resolved_project = await resolve_project(
                                    db,
                                    plan,
                                    batch_project_id=historical[0].batch_project_id,
                                )
                            else:
                                resolved_project = await resolve_project(
                                    db, plan, batch_project_id=None
                                )
                        else:
                            if resolved_project.batch_project_id is not None:
                                applications = [
                                    *applications,
                                    *await active_applications_for_project(
                                        db,
                                        user_id=user_id,
                                        plan_id=plan_id,
                                        batch_project_id=resolved_project.batch_project_id,
                                    ),
                                ]
                    else:
                        try:
                            resolved_project = await resolve_enrollable_project(
                                db, plan, None
                            )
                        except BusinessException:
                            historical = await active_applications_for_plan(
                                db, user_id=user_id, plan_id=plan_id
                            )
                            if (
                                len(historical) == 1
                                and historical[0].batch_project_id is not None
                            ):
                                applications = historical
                                resolved_project = await resolve_project(
                                    db,
                                    plan,
                                    batch_project_id=historical[0].batch_project_id,
                                )
                            else:
                                raise
                        else:
                            if resolved_project.batch_project_id is not None:
                                applications = await active_applications_for_project(
                                    db,
                                    user_id=user_id,
                                    plan_id=plan_id,
                                    batch_project_id=resolved_project.batch_project_id,
                                    include_legacy=True,
                                )
                if len(applications) > 1:
                    raise ConflictException(
                        "该项目存在多条活跃报名，请联系管理员整理后再上传材料"
                    )
                application = applications[0] if applications else None
                if application is not None and application.batch_project_id is not None:
                    resolved_project = await resolve_project(
                        db,
                        plan,
                        batch_project_id=application.batch_project_id,
                    )
                config = await self._project_config(db, plan, resolved_project)
                upload_kinds = {item["kind"] for item in config["materials"]} - {
                    "id_card_front", "id_card_back", "portrait"
                }
                if not is_self_reported(config) or kind not in upload_kinds:
                    raise BusinessException("当前批次不支持该报名材料上传")
                if (
                    application is not None
                    and application.batch_project_id is None
                    and resolved_project.batch_project_id is not None
                ):
                    application.batch_project_id = resolved_project.batch_project_id
                self._validate_edit_window(
                    plan,
                    application,
                    paid_order=await self._paid_correction_order(db, application),
                    now=await db_now_utc(db),
                    config=config,
                )
                if application is None:
                    if not is_v2(config.get("reporting_policy", {})):
                        raise ConflictException("历史批次仅办理已有报名，新报名请选择当前批次")
                    application = RensheApplication(
                        user_id=user_id,
                        plan_id=plan_id,
                        batch_project_id=resolved_project.batch_project_id,
                        status="draft",
                        draft_data={},
                    )
                    db.add(application)
                    await db.flush()
                if application.status not in EDITABLE_APPLICATION_STATUSES or application.frozen_at is not None:
                    raise ConflictException("当前报名不可修改材料")
                await self._verified_profiles(db, user_id, plan, config)
                stored = await self.storage.save_source(user_id=user_id, kind=kind, filename=filename, content_type=content_type, data=content)
                requirement = next((item for item in config["materials"] if item["kind"] == kind), None)
                if requirement is None:
                    raise BusinessException("当前批次不支持该报名材料上传")
                if stored.extension not in requirement["extensions"]:
                    raise BusinessException("文件格式不符合本批次要求")
                values = dict(application.draft_data or {})
                materials = dict(values.get("materials", {}))
                old_key = materials.get(kind, {}).get("storage_key")
                metadata = asdict(stored)
                metadata.pop("source_storage_key", None)
                materials[kind] = metadata
                values["materials"] = materials
                application.draft_data = values
                db.add(self._audit(user_id, "application.material.upload", application, {"kind": kind, "size_bytes": stored.size_bytes}))
                await db.commit()
                committed = True
                result = {"application_id": application.id, **metadata}
        finally:
            if stored is not None and not committed:
                await self.storage.delete_many([stored.storage_key])
        if old_key:
            await delete_unreferenced_source_keys(self.storage, [old_key])
        return result

    async def list_applications(
        self,
        user_id: int,
        *,
        plan_id: int | None = None,
        batch_project_id: int | None = None,
        status: RensheApplicationStatus | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedData[RensheApplicationListItem]:
        """List only the signed-in user's applications for client recovery."""

        async with get_db_ctx() as db:
            from sqlalchemy import and_, exists, or_

            from app.domain.renshe.src.index import RensheBatchProject

            filters = [
                RensheApplication.user_id == user_id,
                or_(
                    Plan.product_type.in_(
                        select(CertProduct.code).where(CertProduct.type == "renshe")
                    ),
                    and_(
                        Plan.product_type.is_(None),
                        exists().where(
                            RensheBatchProject.plan_id == Plan.id
                        ),
                    ),
                ),
            ]
            if plan_id is not None:
                filters.append(RensheApplication.plan_id == plan_id)
            if batch_project_id is not None:
                filters.append(
                    RensheApplication.batch_project_id == batch_project_id
                )
            if status is not None:
                filters.append(RensheApplication.status == status)

            total = (
                await db.scalar(
                    select(func.count())
                    .select_from(RensheApplication)
                    .join(Plan, Plan.id == RensheApplication.plan_id)
                    .where(*filters)
                )
                or 0
            )
            rows = (
                await db.execute(
                    select(RensheApplication, Plan)
                    .join(Plan, Plan.id == RensheApplication.plan_id)
                    .where(*filters)
                    .order_by(
                        RensheApplication.updated_at.desc(),
                        RensheApplication.id.desc(),
                    )
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).all()
            if not rows:
                return PaginatedData(
                    items=[], total=total, page=page, page_size=page_size
                )

            application_ids = [application.id for application, _plan in rows]
            orders = (
                await db.execute(
                    select(Order)
                    .where(
                        Order.application_id.in_(application_ids),
                        Order.user_id == user_id,
                    )
                    .order_by(Order.application_id, Order.id)
                )
            ).scalars().all()
            refunds = (
                await db.execute(
                    select(RensheRefundRequest)
                    .where(
                        RensheRefundRequest.application_id.in_(application_ids),
                        RensheRefundRequest.user_id == user_id,
                    )
                    .order_by(
                        RensheRefundRequest.application_id,
                        RensheRefundRequest.id,
                    )
                )
            ).scalars().all()

            latest_orders = {
                order.application_id: order
                for order in orders
                if order.application_id is not None
            }
            latest_refunds = {refund.application_id: refund for refund in refunds}
            items: list[RensheApplicationListItem] = []
            for application, plan in rows:
                order = latest_orders.get(application.id)
                refund = latest_refunds.get(application.id)
                items.append(
                    RensheApplicationListItem(
                        **RensheApplicationResponse.model_validate(
                            application
                        ).model_dump(),
                        plan=await self._application_plan_summary(db, plan, application),
                        current_order=(
                            RensheApplicationOrderSummary(
                                id=order.id,
                                status=order.status,
                                price_cents=order.price,
                                expires_at=order.expires_at,
                                paid_at=order.paid_at,
                                closed_at=order.closed_at,
                            )
                            if order is not None
                            else None
                        ),
                        current_refund=(
                            RensheApplicationRefundSummary(
                                id=refund.id,
                                status=refund.status,
                                request_kind=refund.request_kind,
                                amount_cents=refund.amount_cents,
                                requested_at=refund.requested_at,
                                due_at=refund.due_at,
                                out_refund_no=refund.out_refund_no,
                                succeeded_at=refund.succeeded_at,
                                last_error=refund.last_error,
                                retry_count=refund.retry_count,
                            )
                            if refund is not None
                            else None
                        ),
                    )
                )
            return PaginatedData(
                items=items,
                total=total,
                page=page,
                page_size=page_size,
            )

    @staticmethod
    async def _application_plan_summary(
        db, plan, application
    ) -> RensheApplicationPlanSummary:
        """报名展示优先使用所选项目的快照，而不是多项目批次的空 Plan 字段。"""
        summary = RensheApplicationPlanSummary.model_validate(plan)
        project_id = getattr(application, "batch_project_id", None)
        if project_id is None:
            if plan.product_type is None:
                raise ConflictException("报名缺少报考项目归属，请联系管理员核实")
            return summary
        from app.models.renshe_project import RensheProjectConfig

        try:
            resolution = await resolve_project(
                db, plan, batch_project_id=project_id
            )
        except BusinessException as exc:
            raise ConflictException(f"报名项目数据异常：{exc}") from exc
        config = None
        if resolution.config_id is not None:
            row = await db.get(RensheProjectConfig, resolution.config_id)
            if row is None:
                raise ConflictException("报名项目配置版本不存在，请联系管理员核实")
            config = row.config
        elif plan.renshe_config_id is None:
            raise ConflictException("报名项目缺少配置版本，请联系管理员核实")
        payload = config or {}
        return summary.model_copy(
            update={
                "price_cents": resolution.price_cents,
                "occupation_name": resolution.occupation_name or summary.occupation_name,
                "occupation_code": resolution.occupation_code or summary.occupation_code,
                "skill_level": resolution.skill_level or summary.skill_level,
                "application_type": (payload.get("application_types") or [summary.application_type])[0],
                "exam_type": (payload.get("exam_types") or [summary.exam_type])[0],
            }
        )

    async def get_application(
        self, user_id: int, application_id: int
    ) -> RensheApplicationDetailResponse:
        async with get_db_ctx() as db:
            application = await db.scalar(
                select(RensheApplication).where(
                    RensheApplication.id == application_id,
                    RensheApplication.user_id == user_id,
                )
            )
            if application is None:
                raise NotFoundException("人社报名")
            plan = await db.get(Plan, application.plan_id)
            versions = (
                await db.execute(
                    select(RensheApplicationVersion)
                    .where(RensheApplicationVersion.application_id == application.id)
                    .order_by(RensheApplicationVersion.version_no.desc())
                )
            ).scalars().all()
            order = await db.scalar(
                select(Order)
                .where(Order.application_id == application.id)
                .order_by(Order.id.desc())
                .limit(1)
            )
            refund = await db.scalar(
                select(RensheRefundRequest)
                .where(RensheRefundRequest.application_id == application.id)
                .order_by(RensheRefundRequest.id.desc())
                .limit(1)
            )
            rejection = None
            if application.current_version_id is not None:
                rejection = await db.scalar(
                    select(RensheReview)
                    .where(
                        RensheReview.application_id == application.id,
                        RensheReview.version_id == application.current_version_id,
                        RensheReview.decision == "rejected",
                    )
                    .order_by(RensheReview.id.desc())
                    .limit(1)
                )
            return RensheApplicationDetailResponse(
                **RensheApplicationResponse.model_validate(application).model_dump(),
                plan=await self._application_plan_summary(db, plan, application),
                versions=[RensheVersionSummary.model_validate(item) for item in versions],
                current_order_id=order.id if order else None,
                current_order_status=order.status if order else None,
                current_refund_id=refund.id if refund else None,
                current_refund_status=refund.status if refund else None,
                current_refund_last_error=refund.last_error if refund else None,
                current_refund_retry_count=refund.retry_count if refund else None,
                latest_rejection_stage=rejection.stage if rejection else None,
                latest_rejection_reason=rejection.reason if rejection else None,
                required_changes=rejection.required_changes if rejection else None,
                consultation_group=(
                    await ConsultationGroupService.get_user_group(
                        db,
                        plan=plan,
                        registration_status=application.status,
                    )
                    if plan is not None
                    else None
                ),
            )

    async def cancel_pending_payment(self, user_id: int, application_id: int) -> None:
        async with get_db_ctx() as db:
            order = await db.scalar(
                select(Order)
                .where(
                    Order.application_id == application_id,
                    Order.user_id == user_id,
                    Order.status == "pending",
                )
                .with_for_update()
            )
            application = await db.scalar(
                select(RensheApplication)
                .where(
                    RensheApplication.id == application_id,
                    RensheApplication.user_id == user_id,
                )
                .with_for_update()
            )
            if application is None:
                raise NotFoundException("人社报名")
            if application.status != "pending_payment":
                raise ConflictException("当前报名不在待支付状态")
            if order is None:
                raise ConflictException("待支付订单不存在")
            now = self._now()
            from app.services.offline_payment import assert_cancellable
            assert_cancellable(order)
            apply_order_status_transition(order, "closed")
            order.closed_at = now
            order.close_reason = "user_cancelled"
            assert_application_transition(application.status, "draft")
            application.status = "draft"
            if application.current_version_id:
                version = await db.get(
                    RensheApplicationVersion, application.current_version_id
                )
                if version is not None:
                    application.draft_data = dict(version.form_data)
            db.add(
                self._audit(
                    user_id,
                    "application.cancel_payment",
                    application,
                    {"order_id": order.id},
                )
            )
            await db.commit()

    @staticmethod
    async def _lock_active_user(db, user_id: int) -> User:
        user = await db.scalar(
            select(User)
            .where(User.id == user_id, User.is_active.is_(True))
            .with_for_update()
        )
        if user is None:
            raise NotFoundException("用户")
        return user

    @staticmethod
    async def _admission_values(db, application, realname, config: dict, form: dict, *, submitted_at=None) -> dict:
        from datetime import timezone
        from zoneinfo import ZoneInfo
        from app.services.renshe_employment import is_level_five
        age_as_of = None
        if is_level_five(config):
            first = await db.scalar(select(func.min(RensheApplicationVersion.submitted_at)).where(RensheApplicationVersion.application_id == application.id))
            if first is not None:
                age_as_of = (first if first.tzinfo else first.replace(tzinfo=timezone.utc)).astimezone(ZoneInfo("Asia/Shanghai")).date()
        assessment_date = submitted_at.astimezone(ZoneInfo("Asia/Shanghai")).date() if submitted_at else None
        return validate_admission(form, config, as_of=assessment_date, verified_birth_date=realname.birth_date, age_as_of=age_as_of)

    @staticmethod
    async def _verified_profiles(
        db,
        user_id: int,
        plan: Plan | None = None,
        config: dict | None = None,
    ) -> tuple[UserRealname, UserStudent | None]:
        if config is None:
            config = batch_configuration(plan) if plan else default_configuration()
        realname = await db.scalar(
            select(UserRealname).where(
                UserRealname.user_id == user_id,
                UserRealname.status == "verified",
                *([] if is_self_reported(config) else [UserRealname.user_type == "student"]),
            )
        )
        if realname is None:
            raise BusinessException("请先完成并通过实名认证")
        required_realname = (
            realname.real_name,
            realname.id_card_number,
            realname.id_card_front_oss,
            realname.id_card_back_oss,
            *(
                ([realname.avatar_oss] if any(item["kind"] == "portrait" and item["required"] for item in config["materials"]) else [])
                if is_self_reported(config)
                else [realname.avatar_oss, realname.ethnicity, realname.political_status]
            ),
        )
        if not all(required_realname):
            raise BusinessException("已通过的实名认证资料不完整，请重新提交审核")
        if is_self_reported(config):
            from datetime import date
            try:
                date.fromisoformat(realname.birth_date or "")
                if realname.gender not in {"男", "女"}:
                    raise ValueError()
            except ValueError as exc:
                raise BusinessException("实名性别或出生日期不完整，请通过实名更正流程补齐") from exc
        digest = identity_hash(realname.id_card_number)
        occupied_by = await db.scalar(
            select(UserRealname.user_id)
            .join(User, User.id == UserRealname.user_id)
            .where(
                UserRealname.user_id != user_id,
                User.is_active.is_(True),
                UserRealname.status.in_(("pending", "verified")),
                (
                    (UserRealname.active_id_card_hash == digest)
                    | (UserRealname.id_card_number == realname.id_card_number)
                ),
            )
            .limit(1)
        )
        if occupied_by is not None:
            raise BusinessException("该身份证号已绑定其他有效账号")
        realname.id_card_hash = digest
        realname.active_id_card_hash = digest
        if is_self_reported(config):
            return realname, None

        student = await db.scalar(
            select(UserStudent).where(
                UserStudent.user_id == user_id,
                UserStudent.status == "verified",
            )
        )
        if student is None:
            raise BusinessException("请先完成并通过学生信息认证")
        required_student = (
            student.education,
            student.school,
            student.major,
            student.enrollment_date,
        )
        if not all(required_student) or student.education not in EDUCATION_LEVELS:
            raise BusinessException("已通过的学生信息不完整，请重新提交审核")
        if student.education not in config["education_levels"]:
            raise BusinessException("当前学历不符合本批次报考要求")
        sources = RensheApplicationService._material_sources(realname, student)
        for requirement in config["materials"]:
            if requirement["required"] and not sources.get(requirement["kind"]):
                raise BusinessException(f"缺少本批次必填材料：{requirement['name']}")
        return realname, student

    @staticmethod
    async def _ensure_capacity(db, plan: Plan) -> None:
        if plan.capacity <= 0:
            return
        occupied = await db.scalar(
            select(func.count())
            .select_from(Order)
            .where(
                Order.plan_id == plan.id,
                Order.status.in_(CAPACITY_OCCUPYING_ORDER_STATUSES),
            )
        )
        if (occupied or 0) >= plan.capacity:
            raise BusinessException("该批次名额已满")

    @staticmethod
    def _realname_snapshot(realname: UserRealname) -> dict:
        return {
            "real_name": realname.real_name,
            "id_card_number": realname.id_card_number,
            "gender": realname.gender,
            "birth_date": realname.birth_date,
            "ethnicity": realname.ethnicity,
            "political_status": realname.political_status,
            "user_type": realname.user_type,
        }

    @staticmethod
    def _student_snapshot(student: UserStudent) -> dict:
        return {
            "education": student.education,
            "school": student.school,
            "major": student.major,
            "enrollment_date": student.enrollment_date.isoformat(),
            "student_status": "current_graduating_student",
            "candidate_source": "院校学生",
        }

    @staticmethod
    def _material_sources(realname: UserRealname, student: UserStudent) -> dict[str, str]:
        return {
            "id_card_front": realname.id_card_front_oss,
            "id_card_back": realname.id_card_back_oss,
            "portrait": realname.avatar_oss,
            "student_card": student.student_card_oss,
            "xuexin_registration": student.enrollment_pdf_oss,
            "education_proof": student.degree_cert_oss,
        }

    @staticmethod
    def _audit(
        user_id: int,
        action: str,
        application: RensheApplication,
        summary: dict,
    ) -> RensheAuditLog:
        return RensheAuditLog(
            actor_type="user",
            actor_id=user_id,
            action=action,
            object_type="application",
            object_id=application.id,
            application_id=application.id,
            version_id=application.current_version_id,
            result="succeeded",
            summary=summary,
        )
