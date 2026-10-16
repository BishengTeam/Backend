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
from app.services.consultation_group import ConsultationGroupService
from app.services.plan_enrollment import CAPACITY_OCCUPYING_ORDER_STATUSES, PlanEnrollmentService
from app.services.renshe_project import batch_configuration, is_renshe_plan, default_configuration
from app.services.renshe_graduated import is_graduated, validate_education
from app.services.renshe_reporting import resolve_contract, validate_reporting, education_config, validate_snapshot, is_v2
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
    def _assert_supported_program(plan: Plan) -> None:
        config = batch_configuration(plan)
        if is_graduated(config):
            from app.services.renshe_graduated import template_path
            template_path(config["reporting_policy"])

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
            self._assert_supported_program(plan)

            application = await db.scalar(
                select(RensheApplication)
                .where(
                    RensheApplication.user_id == user_id,
                    RensheApplication.plan_id == data.plan_id,
                    RensheApplication.status != "closed",
                )
                .with_for_update()
            )
            if plan.renshe_config_id is None:
                raise ConflictException("批次尚未确认项目配置，请联系管理员核实历史归属")
            if application is None and not is_graduated(batch_configuration(plan)):
                raise ConflictException("本期新报名仅开放已毕业学历型批次；历史报名请从我的报名继续办理")
            if application is None and not is_v2(batch_configuration(plan).get("reporting_policy", {})):
                raise ConflictException("旧报送规则批次不再新建报名，请选择模板填报第二版批次")
            self._validate_edit_window(plan, application, paid_order=await self._paid_correction_order(db, application))
            if application is None:
                application = RensheApplication(
                    user_id=user_id,
                    plan_id=data.plan_id,
                    status="draft",
                )
                db.add(application)
                await db.flush()
            if application.status not in EDITABLE_APPLICATION_STATUSES:
                raise ConflictException("当前报名状态不可编辑")
            if application.frozen_at is not None:
                raise ConflictException("退款处理中，报名已冻结")
            await self._verified_profiles(db, user_id, plan)

            values = data.model_dump(mode="json", exclude_none=True)
            if is_graduated(batch_configuration(plan)):
                if data.reporting_config_id is None:
                    raise BusinessException("请先加载并确认本批次的模板填报口径")
                contract = await resolve_contract(db, plan)
                if data.reporting_config_id != contract["config_id"]:
                    raise ConflictException("报送配置已变化或不属于当前批次，请重新加载核对")
                values["education_info"] = validate_education(values.get("education_info"), education_config(batch_configuration(plan)))
                values["reporting_info"] = validate_reporting(values.get("reporting_info"), contract["config"]["reporting_policy"])
                values["reporting_contract"] = {"config_id": contract["config_id"], "policy": contract["config"]["reporting_policy"], "attachment_layout": contract["config"]["attachment_layout"]}
                values["materials"] = (application.draft_data or {}).get("materials", {})
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
            return RensheApplicationResponse.model_validate(application)

    async def submit(
        self, user_id: int, application_id: int
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
                self._assert_supported_program(plan)
                if plan.renshe_config_id is None:
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

                self._validate_edit_window(plan, application, paid_order=paid_order)
                realname, student = await self._verified_profiles(db, user_id, plan)
                config = batch_configuration(plan)
                graduated = is_graduated(config)
                if graduated:
                    try:
                        RensheDraftUpsert.model_validate({key: value for key, value in application.draft_data.items() if key not in {"materials", "reporting_contract"}})
                    except ValueError as exc:
                        raise BusinessException("请先完整填写学历信息和联系方式并保存") from exc
                    policy = await validate_snapshot(db, plan, application.draft_data)
                    validate_reporting(application.draft_data.get("reporting_info"), policy)
                    validate_education(application.draft_data.get("education_info"), education_config(config))

                if application.status != "draft" and paid_order is None:
                    raise ConflictException("驳回重提缺少原已支付订单")

                if paid_order is None:
                    await self._ensure_capacity(db, plan)

                latest_no = await db.scalar(
                    select(func.max(RensheApplicationVersion.version_no)).where(
                        RensheApplicationVersion.application_id == application.id
                    )
                )
                version_no = (latest_no or 0) + 1
                now = self._now()
                version = RensheApplicationVersion(
                    application_id=application.id,
                    version_no=version_no,
                    project_config_id=plan.renshe_config_id,
                    submitted_by_user_id=user_id,
                    realname_snapshot=self._realname_snapshot(realname),
                    student_snapshot={} if graduated else self._student_snapshot(student),
                    form_data=dict(application.draft_data),
                    submitted_at=now,
                )
                db.add(version)
                await db.flush()

                source_materials = ({
                    "id_card_front": realname.id_card_front_oss,
                    "id_card_back": realname.id_card_back_oss,
                    **{kind: metadata["storage_key"] for kind, metadata in application.draft_data.get("materials", {}).items()},
                } if graduated else self._material_sources(realname, student))
                for requirement in config["materials"]:
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
                        product_type=plan.product_type,
                        plan_id=plan.id,
                        application_id=application.id,
                        candidate_name=realname.real_name,
                        candidate_phone=draft["contact_phone"],
                        candidate_idcard=realname.id_card_number,
                        price=plan.price_cents,
                        status="pending",
                        out_trade_no=generate_out_trade_no("RS"),
                        expires_at=now + timedelta(minutes=RENSHE_ORDER_EXPIRE_MINUTES),
                        extra_data={"application_version_id": version.id, "business_type": "renshe", "project_name": plan.project_name, "project_config_id": plan.renshe_config_id},
                    )
                    db.add(order)
                    await db.flush()
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
        if any(order.plan_id != plan.id or order.user_id != user_id or order.product_type != plan.product_type for order in orders):
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
    def _validate_edit_window(cls, plan, application, *, paid_order=None):
        config = batch_configuration(plan)
        if (is_graduated(config) and application is not None
                and application.status in {"initial_rejected", "external_rejected"}):
            if plan.status not in {"published", "registration_closed"} or application.closed_at is not None:
                raise ConflictException("批次或报名已关闭，不能补正")
            deadline = application.correction_due_at
            if deadline is None or PlanEnrollmentService._as_utc(deadline) < cls._now():
                raise ConflictException("补正期限已过或尚未设置，请联系管理员")
            if paid_order is None:
                raise ConflictException("补正缺少原已支付订单，请联系管理员")
            return
        PlanEnrollmentService.validate_application_window(plan)

    @staticmethod
    async def _paid_correction_order(db, application):
        if application is None or application.status not in {"initial_rejected", "external_rejected"}:
            return None
        return await db.scalar(select(Order).where(
            Order.application_id == application.id, Order.plan_id == application.plan_id,
            Order.user_id == application.user_id, Order.status.in_(("paid", "completed"))).limit(1))

    async def upload_application_material(self, user_id: int, plan_id: int, kind: str, *, filename: str, content_type: str | None, content: bytes) -> dict:
        from app.services.renshe_graduated import MATERIALS
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
                config = batch_configuration(plan)
                if not is_graduated(config) or kind not in set(MATERIALS) - {"id_card_front", "id_card_back"}:
                    raise BusinessException("当前批次不支持该报名材料上传")
                application = await db.scalar(select(RensheApplication).where(
                    RensheApplication.user_id == user_id, RensheApplication.plan_id == plan_id,
                    RensheApplication.status != "closed").with_for_update())
                self._validate_edit_window(plan, application, paid_order=await self._paid_correction_order(db, application))
                if application is None:
                    if not is_v2(config.get("reporting_policy", {})):
                        raise ConflictException("旧报送规则批次不再新建报名，请选择模板填报第二版批次")
                    application = RensheApplication(user_id=user_id, plan_id=plan_id, status="draft", draft_data={})
                    db.add(application)
                    await db.flush()
                if application.status not in EDITABLE_APPLICATION_STATUSES or application.frozen_at is not None:
                    raise ConflictException("当前报名不可修改材料")
                await self._verified_profiles(db, user_id, plan)
                stored = await self.storage.save_source(user_id=user_id, kind=kind, filename=filename, content_type=content_type, data=content)
                requirement = next(item for item in config["materials"] if item["kind"] == kind)
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
        status: RensheApplicationStatus | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedData[RensheApplicationListItem]:
        """List only the signed-in user's applications for client recovery."""

        async with get_db_ctx() as db:
            filters = [
                RensheApplication.user_id == user_id,
                Plan.product_type.in_(select(CertProduct.code).where(CertProduct.type == "renshe")),
            ]
            if plan_id is not None:
                filters.append(RensheApplication.plan_id == plan_id)
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
                        plan=RensheApplicationPlanSummary.model_validate(plan),
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
                plan=RensheApplicationPlanSummary.model_validate(plan),
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
    async def _verified_profiles(db, user_id: int, plan: Plan | None = None) -> tuple[UserRealname, UserStudent | None]:
        config = batch_configuration(plan) if plan else default_configuration()
        realname = await db.scalar(
            select(UserRealname).where(
                UserRealname.user_id == user_id,
                UserRealname.status == "verified",
                *([] if is_graduated(config) else [UserRealname.user_type == "student"]),
            )
        )
        if realname is None:
            raise BusinessException("请先完成并通过实名认证")
        required_realname = (
            realname.real_name,
            realname.id_card_number,
            realname.id_card_front_oss,
            realname.id_card_back_oss,
            *([] if is_graduated(config) else [realname.avatar_oss, realname.ethnicity, realname.political_status]),
        )
        if not all(required_realname):
            raise BusinessException("已通过的实名认证资料不完整，请重新提交审核")
        if is_graduated(config):
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
        if is_graduated(config):
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
