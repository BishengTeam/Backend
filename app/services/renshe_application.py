from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.domain.order.src.index import Order, apply_order_status_transition
from app.domain.plan.src.index import Plan
from app.domain.renshe.src.index import (
    EDUCATION_LEVELS,
    MATERIAL_SPECS,
    RENSHE_ORDER_EXPIRE_MINUTES,
    RensheApplication,
    RensheApplicationVersion,
    RensheAuditLog,
    RensheMaterial,
    RensheRefundRequest,
    RensheReview,
    assert_application_transition,
)
from app.domain.user.src.index import User, UserRealname, UserStudent
from app.integrations.renshe_storage import RensheObjectStorage
from app.port.exceptions import BusinessException, ConflictException, NotFoundException
from app.schemas.renshe import (
    RensheApplicationDetailResponse,
    RensheApplicationResponse,
    RensheApplicationSubmitResponse,
    RensheDraftUpsert,
    RensheVersionSummary,
)
from app.services.plan_enrollment import CAPACITY_OCCUPYING_ORDER_STATUSES, PlanEnrollmentService
from app.utils.pii import identity_hash


logger = logging.getLogger(__name__)
EDITABLE_APPLICATION_STATUSES = {"draft", "initial_rejected", "external_rejected"}


class RensheApplicationService:
    def __init__(self, storage: RensheObjectStorage | None = None) -> None:
        self.storage = storage or RensheObjectStorage()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    async def save_draft(
        self, user_id: int, data: RensheDraftUpsert
    ) -> RensheApplicationResponse:
        async with get_db_ctx() as db:
            await self._lock_active_user(db, user_id)
            plan = await db.scalar(
                select(Plan).where(Plan.id == data.plan_id).with_for_update()
            )
            if plan is None or plan.product_type != "RS-ZY":
                raise NotFoundException("人社报名批次")
            PlanEnrollmentService.validate_application_window(plan)

            application = await db.scalar(
                select(RensheApplication)
                .where(
                    RensheApplication.user_id == user_id,
                    RensheApplication.plan_id == data.plan_id,
                    RensheApplication.status != "closed",
                )
                .with_for_update()
            )
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
            await self._verified_profiles(db, user_id)

            application.draft_data = data.model_dump(mode="json")
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
                if application.frozen_at is not None:
                    raise ConflictException("退款处理中，报名已冻结")
                if not application.draft_data:
                    raise BusinessException("请先保存完整报名草稿")

                PlanEnrollmentService.validate_application_window(plan)
                realname, student = await self._verified_profiles(db, user_id)

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
                    submitted_by_user_id=user_id,
                    realname_snapshot=self._realname_snapshot(realname),
                    student_snapshot=self._student_snapshot(student),
                    form_data=dict(application.draft_data),
                    submitted_at=now,
                )
                db.add(version)
                await db.flush()

                source_materials = self._material_sources(realname, student)
                for kind in MATERIAL_SPECS:
                    copied = await self.storage.copy_version_material(
                        user_id=user_id,
                        plan_id=plan.id,
                        application_id=application.id,
                        version_no=version_no,
                        kind=kind,
                        source_key=source_materials[kind],
                    )
                    copied_keys.append(copied.storage_key)
                    db.add(
                        RensheMaterial(
                            application_id=application.id,
                            version_id=version.id,
                            kind=copied.kind,
                            source_storage_key=copied.source_storage_key,
                            storage_key=copied.storage_key,
                            original_filename=copied.original_filename,
                            extension=copied.extension,
                            content_type=copied.content_type,
                            size_bytes=copied.size_bytes,
                            sha256=copied.sha256,
                        )
                    )

                application.current_version_id = version.id
                application.submitted_at = now

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
                        product_type="RS-ZY",
                        plan_id=plan.id,
                        application_id=application.id,
                        candidate_name=realname.real_name,
                        candidate_phone=draft["contact_phone"],
                        candidate_idcard=realname.id_card_number,
                        price=plan.price_cents,
                        status="pending",
                        out_trade_no=(
                            f"RS{user_id}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
                        ),
                        expires_at=now + timedelta(minutes=RENSHE_ORDER_EXPIRE_MINUTES),
                        extra_data={"application_version_id": version.id},
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
                except Exception:
                    logger.exception("failed to remove orphaned renshe version materials")
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
        if plan is None or plan.product_type != "RS-ZY":
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

        paid_order = next(
            (
                order
                for order in reversed(orders)
                if order.status in {"paid", "completed"}
            ),
            None,
        )
        return application, plan, paid_order

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
                versions=[RensheVersionSummary.model_validate(item) for item in versions],
                current_order_id=order.id if order else None,
                current_order_status=order.status if order else None,
                current_refund_id=refund.id if refund else None,
                current_refund_status=refund.status if refund else None,
                latest_rejection_stage=rejection.stage if rejection else None,
                latest_rejection_reason=rejection.reason if rejection else None,
                required_changes=rejection.required_changes if rejection else None,
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
    async def _verified_profiles(db, user_id: int) -> tuple[UserRealname, UserStudent]:
        realname = await db.scalar(
            select(UserRealname).where(
                UserRealname.user_id == user_id,
                UserRealname.status == "verified",
                UserRealname.user_type == "student",
            )
        )
        if realname is None:
            raise BusinessException("请先完成并通过实名认证")
        required_realname = (
            realname.real_name,
            realname.id_card_number,
            realname.id_card_front_oss,
            realname.id_card_back_oss,
            realname.avatar_oss,
            realname.ethnicity,
            realname.political_status,
        )
        if not all(required_realname):
            raise BusinessException("已通过的实名认证资料不完整，请重新提交审核")
        digest = identity_hash(realname.id_card_number)
        occupied_by = await db.scalar(
            select(UserRealname.user_id)
            .join(User, User.id == UserRealname.user_id)
            .where(
                UserRealname.user_id != user_id,
                User.is_active.is_(True),
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
            student.student_card_oss,
            student.enrollment_pdf_oss,
            student.degree_cert_oss,
        )
        if not all(required_student) or student.education not in EDUCATION_LEVELS:
            raise BusinessException("已通过的学生信息不完整，请重新提交审核")
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
            "user_type": "student",
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
