"""NISP registration service: order creation, review, export-ready snapshots."""

import re
from uuid import uuid4
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select, update as sa_update

from app.adapter.database import get_db_ctx
from app.domain.order.src.index import Order
from app.domain.nisp.src import (
    NispExamBatch,
    NispMaterialFile,
    NispRegistration,
    NispReview,
    NispRefundRequest,
    NispRegistrationVersion,
    NispCorrectionRequest,
    NispExportItem,
    NispExportJob,
)
from app.integrations.nisp_storage import NispObjectStorage
from app.domain.plan.src.index import Plan
from app.domain.user.src.index import User, UserRealname
from app.port.exceptions import BusinessException, ConflictException, NotFoundException
from app.schemas.nisp import (
    NispOrderCreate,
    NispRegistrationResponse,
    NispReviewResponse,
    NispReviewDecisionRequest,
    NispResubmitRequest,
    NispMaterialResponse,
    NispRejectRefundRequest,
    NispCorrectionRequestResponse,
    NispRegistrationVersionResponse,
)
NISP_CORRECTABLE_FIELDS = {
    "pinyin", "phone", "email", "school", "major", "province",
    "gender", "age", "education", "address", "zip_code",
}
from app.services.points_mall import release_order_coupon
from app.services.certification_identity import (
    assert_submitted_identity,
    lock_verified_identity,
    validate_nisp_batch_product,
    verified_identity_snapshot,
)
from app.services.plan_enrollment import PlanEnrollmentService
from app.services.nisp_lifecycle import (
    ACTIVE_REGISTRATION_STATUSES, occupied_count, occupied_orders, request_refund,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _birth_date_from_idcard(idcard: str) -> str:
    try:
        year = int(idcard[6:10])
        month = int(idcard[10:12])
        day = int(idcard[12:14])
        return f"{year:04d}-{month:02d}-{day:02d}"
    except (ValueError, IndexError):
        return ""


def _price(batch: NispExamBatch, level: str) -> int:
    if level == "1":
        return batch.level1_price_cents
    return batch.level2_price_cents


def _registration_no(level: str, seq: int) -> str:
    prefix = "NISP-1" if level == "1" else "NISP-2"
    return f"{prefix}-{seq:08d}"


class NispRegistrationService:
    def __init__(self, storage: NispObjectStorage | None = None) -> None:
        self.storage = storage or NispObjectStorage()


    async def create_order(
        self, user_id: int, data: NispOrderCreate
    ) -> NispRegistrationResponse:
        """Create a NISP registration + order in one transaction."""
        from app.domain.order.src.index import Order, apply_order_status_transition
        from app.utils.payment import generate_out_trade_no
        from app.services.agreement_template import ensure_accepted

        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None or not user.is_active:
                raise BusinessException("用户不可用")
            identity = await lock_verified_identity(db, user_id=user_id)
            assert_submitted_identity(
                identity,
                submitted_name=data.name,
                submitted_id_card=data.id_card,
            )
            # Agreement check
            await ensure_accepted(
                db,
                user_id,
                "cert_registration",
                message="请先阅读并同意认证报名信息处理授权协议",
            )

            # Resolve IDs without locking first, then acquire locks in the
            # certification-wide order: Plan -> Batch -> Order -> Registration.
            resolved_plan_id = (
                await db.scalar(
                    select(NispExamBatch.plan_id).where(
                        NispExamBatch.id == data.batch_id
                    )
                )
            )
            if resolved_plan_id is None:
                raise NotFoundException("NISP 考试批次")
            plan = await db.scalar(
                select(Plan).where(Plan.id == resolved_plan_id).with_for_update()
            )
            if plan is None:
                raise NotFoundException("NISP 报名计划")
            batch = (
                await db.execute(
                    select(NispExamBatch)
                    .where(NispExamBatch.id == data.batch_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if batch is None or batch.plan_id != plan.id:
                raise NotFoundException("NISP 考试批次")
            product = await validate_nisp_batch_product(
                db, plan=plan, level=batch.level, require_active=True
            )
            PlanEnrollmentService.validate_application_window(plan)
            if plan.capacity > 0 and await occupied_count(db, plan.id) >= plan.capacity:
                raise ConflictException("该考试批次名额已满")
            if await db.scalar(occupied_orders(plan.id).where(Order.user_id == user_id).limit(1)):
                raise ConflictException("您已报名该批次，请勿重复提交")

            # Check level consistency
            if data.level != batch.level:
                raise ConflictException("报名级别与批次不匹配")

            # Check duplicate registration
            existing = (
                await db.execute(
                    select(NispRegistration).where(
                        NispRegistration.batch_id == batch.id,
                        NispRegistration.candidate_idcard == identity.id_card_number,
                        NispRegistration.status.in_(ACTIVE_REGISTRATION_STATUSES),
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise ConflictException("该身份证号已报名此批次")

            # Validate level-2 required fields
            if data.level == "2":
                for field in ("gender", "age", "education", "address", "zip_code"):
                    if not getattr(data, field):
                        raise BusinessException(f"NISP二级必须填写 {field}")
                if not data.xuexin_report_key:
                    raise BusinessException("NISP二级必须上传学籍验证报告")
                if not data.application_form_key:
                    raise BusinessException("NISP二级必须上传申请表模板")

            price = _price(batch, data.level)

            # Create order
            expires_at = _now() + timedelta(minutes=batch.payment_timeout_minutes)
            order = Order(
                user_id=user_id,
                order_kind="certification",
                product_type=product.code,
                plan_id=plan.id,
                candidate_name=identity.real_name,
                candidate_phone=data.phone,
                candidate_idcard=identity.id_card_number,
                price=price,
                status="pending",
                out_trade_no=generate_out_trade_no("ORD"),
                expires_at=expires_at,
            )
            db.add(order)
            await db.flush()

            # Build snapshot (matching Excel export columns)
            snapshot = self._build_snapshot(data, batch, plan, identity)

            # Build material keys
            material_keys = {
                "id_card_both_sides": data.id_card_both_sides_key,
                "portrait_photo": data.portrait_photo_key,
            }
            if data.level == "2":
                material_keys["xuexin_report"] = data.xuexin_report_key
                material_keys["application_form"] = data.application_form_key

            registration = NispRegistration(
                batch_id=batch.id,
                plan_id=batch.plan_id,
                user_id=user_id,
                order_id=order.id,
                registration_no=uuid4().hex,
                level=data.level,
                status="pending_payment",
                candidate_snapshot=snapshot,
                candidate_idcard=identity.id_card_number,
                material_keys=material_keys,
            )
            db.add(registration)
            await db.flush()
            registration.registration_no = _registration_no(data.level, registration.id)
            if price == 0:
                apply_order_status_transition(order, "completed")
                order.paid_at = _now()
                order.expires_at = None
                registration.status = "pending_review"
            await self._bind_material(
                db,
                user_id=user_id,
                registration_id=registration.id,
                material_type="id_card_both_sides",
                storage_key=data.id_card_both_sides_key,
            )
            await self._bind_material(
                db,
                user_id=user_id,
                registration_id=registration.id,
                material_type="portrait_photo",
                storage_key=data.portrait_photo_key,
            )
            if data.level == "2":
                await self._bind_material(
                    db,
                    user_id=user_id,
                    registration_id=registration.id,
                    material_type="xuexin_report",
                    storage_key=data.xuexin_report_key,
                )
                await self._bind_material(
                    db,
                    user_id=user_id,
                    registration_id=registration.id,
                    material_type="application_form",
                    storage_key=data.application_form_key,
                )
            db.add(NispRegistrationVersion(
                registration_id=registration.id,
                version_no=1,
                candidate_snapshot=snapshot,
                material_versions=material_keys,
                source="initial",
                submitted_at=_now(),
                is_current=True,
            ))
            await db.commit()
            await db.refresh(registration)
            return await self._response(db, registration)

    async def list_user_registrations(
        self, user_id: int, *, page: int = 1, page_size: int = 20
    ) -> dict:
        async with get_db_ctx() as db:
            base = select(NispRegistration).where(
                NispRegistration.user_id == user_id
            )
            total = await db.scalar(
                select(func.count()).select_from(base.subquery())
            ) or 0
            rows = (
                await db.execute(
                    base.order_by(NispRegistration.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            return {
                "items": [await self._response(db, r) for r in rows],
                "total": total,
                "page": page,
                "page_size": page_size,
            }

    async def get_registration(
        self, user_id: int, registration_id: int
    ) -> NispRegistrationResponse:
        async with get_db_ctx() as db:
            reg = await db.get(NispRegistration, registration_id)
            if reg is None or reg.user_id != user_id:
                raise NotFoundException("NISP 报名记录")
            return await self._response(db, reg)

    async def cancel_pending_payment(
        self, user_id: int, registration_id: int
    ) -> NispRegistrationResponse:
        async with get_db_ctx() as db:
            from app.domain.order.src.index import Order, apply_order_status_transition

            order_id = await db.scalar(select(NispRegistration.order_id).where(
                NispRegistration.id == registration_id,
                NispRegistration.user_id == user_id,
            ))
            if order_id is None:
                raise NotFoundException("NISP 报名记录")
            # Use the same order -> registration lock order as payment callbacks
            # and batch cancellation, so concurrent payment/cancel cannot deadlock.
            order = await db.scalar(select(Order).where(Order.id == order_id).with_for_update())
            reg = (
                await db.execute(
                    select(NispRegistration)
                    .where(
                        NispRegistration.id == registration_id,
                        NispRegistration.user_id == user_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if reg is None:
                raise NotFoundException("NISP 报名记录")
            if reg.status != "pending_payment":
                raise ConflictException("当前状态不能取消")

            if order is None or order.status != "pending":
                raise ConflictException("订单已不在待支付状态")
            if order.status == "pending":
                coupon_released = await release_order_coupon(
                    db,
                    order_id=order.id,
                    user_id=order.user_id,
                    coupon_code=order.coupon_code,
                )
                if coupon_released:
                    order.price = order.original_price or order.price
                    order.original_price = None
                    order.discount_amount = None
                    order.coupon_code = None
                apply_order_status_transition(order, "closed")
                order.closed_at = _now()
                order.close_reason = "user_cancelled"

            reg.status = "cancelled"
            reg.closed_at = _now()
            reg.close_reason = "user_cancelled"
            await db.commit()
            await db.refresh(reg)
            return await self._response(db, reg)

    async def resubmit_materials(
        self, user_id: int, registration_id: int, data: NispResubmitRequest
    ) -> NispRegistrationResponse:
        async with get_db_ctx() as db:
            unresolved = await db.scalar(
                select(NispRegistration).where(NispRegistration.id == registration_id)
            )
            if unresolved is None:
                raise NotFoundException("NISP 报名记录")
            batch = await db.scalar(
                select(NispExamBatch)
                .where(NispExamBatch.id == unresolved.batch_id)
                .with_for_update()
            )
            if batch is None:
                raise NotFoundException("NISP 考试批次")
            order = await db.scalar(
                select(Order)
                .where(Order.id == unresolved.order_id)
                .with_for_update()
            )
            if order is None:
                raise ConflictException("NISP 报名缺少订单")
            reg = await db.scalar(
                select(NispRegistration)
                .where(
                    NispRegistration.id == registration_id,
                    NispRegistration.user_id == user_id,
                )
                .with_for_update()
            )
            if reg is None:
                raise NotFoundException("NISP 报名记录")
            if reg.batch_id != batch.id or reg.order_id != order.id:
                raise ConflictException("NISP 报名关联记录不一致")
            if reg.status != "rejected_awaiting_resubmission":
                raise ConflictException("当前状态不能补正")
            if reg.resubmission_count >= batch.max_resubmissions:
                raise ConflictException("补正次数已用完")
            if reg.resubmission_due_at and reg.resubmission_due_at < _now():
                raise ConflictException("补正已超时")

            correction = await db.scalar(
                select(NispCorrectionRequest)
                .where(
                    NispCorrectionRequest.registration_id == reg.id,
                    NispCorrectionRequest.status == "pending",
                )
                .with_for_update()
            )
            if correction is None:
                raise ConflictException("未找到待补正请求")
            allowed_fields = set(correction.allowed_fields or [])
            allowed_materials = set(correction.allowed_material_types or [])
            field_values = {
                "pinyin": data.pinyin,
                "phone": data.phone,
                "email": data.email,
                "school": data.school,
                "major": data.major,
                "province": data.province,
                "gender": data.gender,
                "age": data.age,
                "education": data.education,
                "address": data.address,
                "zip_code": data.zip_code,
            }
            submitted_fields = {
                key: value for key, value in field_values.items() if value is not None
            }
            submitted_materials = {
                key: value
                for key, value in {
                    "id_card_both_sides": data.id_card_both_sides_key,
                    "portrait_photo": data.portrait_photo_key,
                    "xuexin_report": data.xuexin_report_key,
                    "application_form": data.application_form_key,
                }.items()
                if value
            }
            if not set(submitted_fields).issubset(NISP_CORRECTABLE_FIELDS):
                raise BusinessException("包含不允许补正的 NISP 字段")
            if set(submitted_fields) != allowed_fields:
                raise BusinessException("请且只能提交管理员允许修改的信息")
            if set(submitted_materials) != allowed_materials:
                raise BusinessException("请且只能重新上传管理员指定材料")
            level2_only = {"xuexin_report", "application_form"}
            if reg.level != "2" and allowed_materials & level2_only:
                raise BusinessException("仅 NISP 二级允许提交该材料")

            now = _now()
            snapshot = dict(reg.candidate_snapshot or {})
            snapshot.update(submitted_fields)
            keys = dict(reg.material_keys or {})
            for key, value in submitted_materials.items():
                await self._bind_material(
                    db,
                    user_id=user_id,
                    registration_id=reg.id,
                    material_type=key,
                    storage_key=value,
                    replace_current=True,
                )
                keys[key] = value

            current_version = await db.scalar(
                select(NispRegistrationVersion)
                .where(
                    NispRegistrationVersion.registration_id == reg.id,
                    NispRegistrationVersion.is_current.is_(True),
                )
                .with_for_update()
            )
            if current_version is None:
                current_version = NispRegistrationVersion(
                    registration_id=reg.id,
                    version_no=1,
                    candidate_snapshot=dict(reg.candidate_snapshot or {}),
                    material_versions=dict(reg.material_keys or {}),
                    source="initial",
                    submitted_at=reg.created_at or now,
                    is_current=True,
                )
                db.add(current_version)
                await db.flush()
            current_version.is_current = False
            current_version.superseded_at = now
            await db.flush()
            current_materials = (
                await db.execute(
                    select(NispMaterialFile).where(
                        NispMaterialFile.registration_id == reg.id,
                        NispMaterialFile.is_current.is_(True),
                    )
                )
            ).scalars().all()
            new_version = NispRegistrationVersion(
                registration_id=reg.id,
                version_no=current_version.version_no + 1,
                candidate_snapshot=snapshot,
                material_versions={
                    row.material_type: {
                        "material_id": row.id,
                        "version_no": row.version_no,
                        "sha256": row.sha256,
                    }
                    for row in current_materials
                },
                source="user_resubmission",
                submitted_at=now,
                is_current=True,
            )
            db.add(new_version)
            await db.flush()

            correction.status = "submitted"
            correction.submitted_version_id = new_version.id
            correction.submitted_at = now
            reg.candidate_snapshot = snapshot
            reg.material_keys = keys
            reg.resubmission_count += 1
            reg.status = "pending_review"
            reg.resubmission_due_at = None
            await db.execute(
                sa_update(NispExportItem)
                .where(
                    NispExportItem.registration_id == reg.id,
                    NispExportItem.is_valid.is_(True),
                )
                .values(
                    is_valid=False,
                    invalidated_at=now,
                    invalidated_reason="new_registration_version",
                )
            )
            await db.commit()
            await db.refresh(reg)
            return await self._response(db, reg)

    async def review(
        self,
        *,
        admin_id: int,
        registration_id: int,
        data: NispReviewDecisionRequest,
    ) -> NispRegistrationResponse:
        async with get_db_ctx() as db:
            unresolved = await db.scalar(
                select(NispRegistration).where(NispRegistration.id == registration_id)
            )
            if unresolved is None:
                raise NotFoundException("NISP 报名记录")
            batch = await db.scalar(
                select(NispExamBatch)
                .where(NispExamBatch.id == unresolved.batch_id)
                .with_for_update()
            )
            if batch is None:
                raise NotFoundException("NISP 考试批次")
            order = await db.scalar(
                select(Order).where(Order.id == unresolved.order_id).with_for_update()
            )
            if order is None:
                raise ConflictException("NISP 报名缺少订单")
            reg = (
                await db.execute(
                    select(NispRegistration)
                    .where(NispRegistration.id == registration_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if reg is None:
                raise NotFoundException("NISP 报名记录")
            if reg.batch_id != batch.id or reg.order_id != order.id:
                raise ConflictException("NISP 报名关联记录不一致")
            if reg.status not in {"pending_review", "approved"}:
                raise ConflictException("当前状态不能审核")

            now = _now()
            if data.decision == "rejected":
                rejected = set(data.rejected_material_types or [])
                if not data.reason_detail or not data.reason_detail.strip():
                    raise BusinessException("请填写驳回原因")
                if rejected and not rejected.issubset(reg.material_keys or {}):
                    raise BusinessException("请选择本次报名中已有的材料项")
                if reg.level != "2" and rejected & {"xuexin_report", "application_form"}:
                    raise BusinessException("仅 NISP 二级允许补交该材料")
                if not set(data.allowed_fields).issubset(NISP_CORRECTABLE_FIELDS):
                    raise BusinessException("包含不允许补正的 NISP 字段")
                if not rejected and not data.allowed_fields:
                    raise BusinessException("请选择至少一项补正内容")
            review = NispReview(
                registration_id=reg.id,
                decision=data.decision,
                reason_code=data.reason_code,
                reason_detail=data.reason_detail,
                rejected_material_types=data.rejected_material_types or None,
                reviewer_admin_id=admin_id,
                reviewed_at=now,
            )
            db.add(review)
            await db.flush()
            reg.last_reviewed_at = now

            if data.decision == "approved":
                reg.status = "approved"
                reg.approved_at = now
                reg.resubmission_due_at = None
            else:
                reg.rejection_count += 1
                if reg.resubmission_count < (batch.max_resubmissions if batch else 2):
                    reg.status = "rejected_awaiting_resubmission"
                    reg.resubmission_due_at = now + timedelta(
                        hours=batch.resubmission_window_hours if batch else 72
                    )
                    db.add(NispCorrectionRequest(
                        registration_id=reg.id,
                        review_id=review.id,
                        allowed_fields=list(dict.fromkeys(data.allowed_fields)),
                        allowed_material_types=data.rejected_material_types or None,
                        reason_code=data.reason_code or "review_rejected",
                        reason_detail=data.reason_detail,
                        due_at=reg.resubmission_due_at,
                        status="pending",
                        created_by_admin_id=admin_id,
                    ))
                else:
                    # Max resubmissions reached — transition to refund flow
                    await request_refund(
                        db, reg, order,
                        reason_code="review_failed_max_resubmissions",
                        reason_detail=data.reason_detail,
                    )

            await db.commit()
            await db.refresh(reg)
            return await self._response(db, reg)

    async def reject_and_refund(
        self,
        *,
        admin_id: int,
        registration_id: int,
        data: NispRejectRefundRequest,
    ) -> NispRegistrationResponse:
        """Authorize a dedicated NISP refund; a worker performs provider I/O."""
        async with get_db_ctx() as db:
            unresolved = await db.scalar(
                select(NispRegistration).where(NispRegistration.id == registration_id)
            )
            if unresolved is None:
                raise NotFoundException("NISP 报名记录")
            batch = await db.scalar(
                select(NispExamBatch)
                .where(NispExamBatch.id == unresolved.batch_id)
                .with_for_update()
            )
            if batch is None:
                raise NotFoundException("NISP 考试批次")
            order = await db.scalar(
                select(Order)
                .where(Order.id == unresolved.order_id)
                .with_for_update()
            )
            if order is None:
                raise ConflictException("NISP 报名缺少订单")
            reg = await db.scalar(
                select(NispRegistration)
                .where(NispRegistration.id == registration_id)
                .with_for_update()
            )
            if reg is None:
                raise NotFoundException("NISP 报名记录")
            if reg.batch_id != batch.id or reg.order_id != order.id:
                raise ConflictException("NISP 报名关联记录不一致")
            if reg.status not in {
                "pending_review", "approved", "rejected_awaiting_resubmission"
            }:
                raise ConflictException("当前状态不能拒绝并退款")
            if order.status not in {"paid", "completed"}:
                raise ConflictException("NISP 订单不是可退款状态")

            now = _now()
            review = NispReview(
                registration_id=reg.id,
                decision="rejected_refund",
                reason_code=data.reason_code,
                reason_detail=data.reason_detail,
                reviewer_admin_id=admin_id,
                reviewed_at=now,
            )
            db.add(review)
            refund = await request_refund(
                db,
                reg,
                order,
                reason_code=data.reason_code,
                reason_detail=data.reason_detail,
            )
            if refund.status in {"requested", "failed"}:
                await db.flush()
                refund.status = "approved"
                refund.approved_by_admin_id = admin_id
                refund.approved_at = now
                refund.last_error = None
                refund.out_refund_no = f"NISP-RF-{refund.id:08d}"
            reg.last_reviewed_at = now
            reg.status = "refund_processing"
            reg.resubmission_due_at = None
            reg.close_reason = "refund_authorized"
            await db.commit()
            await db.refresh(reg)
            return await self._response(db, reg)

    async def process_resubmission_timeouts(self, *, limit: int = 100) -> int:
        async with get_db_ctx() as db:
            rows = (
                await db.execute(
                    select(Order.id, NispRegistration.id)
                    .join(NispRegistration, NispRegistration.order_id == Order.id)
                    .where(
                        NispRegistration.status == "rejected_awaiting_resubmission",
                        NispRegistration.resubmission_due_at <= _now(),
                    )
                    .order_by(Order.id)
                    .limit(limit)
                )
            ).all()
            processed = 0
            for order_id, registration_id in rows:
                order = await db.scalar(
                    select(Order)
                    .where(Order.id == order_id)
                    .with_for_update(skip_locked=True)
                )
                if order is None:
                    continue
                reg = await db.scalar(
                    select(NispRegistration)
                    .where(NispRegistration.id == registration_id)
                    .with_for_update(skip_locked=True)
                )
                if (
                    reg is None
                    or reg.status != "rejected_awaiting_resubmission"
                    or reg.resubmission_due_at is None
                    or reg.resubmission_due_at > _now()
                ):
                    continue
                await db.execute(
                    sa_update(NispCorrectionRequest)
                    .where(
                        NispCorrectionRequest.registration_id == reg.id,
                        NispCorrectionRequest.status == "pending",
                    )
                    .values(status="expired")
                )
                await request_refund(
                    db,
                    reg,
                    order,
                    reason_code="resubmission_timeout",
                    reason_detail="补交材料已超时",
                )
                processed += 1
            await db.commit()
            return processed

    async def on_order_paid(self, db, order) -> bool:
        """Callback when order is paid: transition to pending_review."""
        reg = (
            await db.execute(
                select(NispRegistration)
                .where(NispRegistration.order_id == order.id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if reg is None or reg.status != "pending_payment":
            return False
        reg.status = "pending_review"
        return True

    def _build_snapshot(
        self,
        data: NispOrderCreate,
        batch: NispExamBatch,
        plan: Plan,
        identity: UserRealname,
    ) -> dict:
        """Build a snapshot matching NISP Excel export columns."""
        snapshot = {
            "name": identity.real_name,
            "pinyin": data.pinyin,
            "major": data.major,
            "school": data.school,
            "id_card": identity.id_card_number,
            "phone": data.phone,
            "email": data.email,
            "province": data.province,
            "training_type": f"NISP{'一级' if data.level == '1' else '二级'}",
            "birth_date": _birth_date_from_idcard(identity.id_card_number),
            "exam_date": plan.exam_date.isoformat() if plan.exam_date else None,
            "exam_location": plan.exam_location,
            "institution": batch.training_org,
            "identity_verification": verified_identity_snapshot(identity),
        }
        if data.level == "2":
            snapshot.update({
                "gender": data.gender,
                "age": data.age,
                "education": data.education,
                "address": data.address,
                "zip_code": data.zip_code,
            })
        return snapshot

    async def _bind_material(
        self,
        db,
        *,
        user_id: int,
        registration_id: int,
        material_type: str,
        storage_key: str | None,
        replace_current: bool = False,
    ) -> NispMaterialFile:
        if not storage_key:
            raise BusinessException("报名材料不能为空")
        material = await db.scalar(
            select(NispMaterialFile)
            .where(NispMaterialFile.storage_key == storage_key)
            .with_for_update()
        )
        if material is None:
            raise BusinessException("报名材料不存在或未完成上传")
        if material.user_id != user_id:
            raise BusinessException("报名材料不属于当前用户")
        if material.material_type != material_type:
            raise BusinessException("材料类型与报名材料不匹配")
        if material.registration_id is not None:
            raise BusinessException("报名材料已绑定其他报名记录")

        if replace_current:
            current_rows = (
                await db.execute(
                    select(NispMaterialFile)
                    .where(
                        NispMaterialFile.registration_id == registration_id,
                        NispMaterialFile.material_type == material_type,
                        NispMaterialFile.is_current.is_(True),
                    )
                    .with_for_update()
                )
            ).scalars().all()
            for current in current_rows:
                current.is_current = False

        max_version = await db.scalar(
            select(func.max(NispMaterialFile.version_no)).where(
                NispMaterialFile.registration_id == registration_id,
                NispMaterialFile.material_type == material_type,
            )
        )
        material.registration_id = registration_id
        material.version_no = (max_version or 0) + 1
        material.is_current = True
        material.bound_at = _now()
        return material

    async def _response(
        self, db, registration: NispRegistration
    ) -> NispRegistrationResponse:
        from app.domain.order.src.index import Order

        order = await db.get(Order, registration.order_id)
        latest_review = await db.scalar(
            select(NispReview)
            .where(NispReview.registration_id == registration.id)
            .order_by(NispReview.id.desc())
            .limit(1)
        )
        materials = []
        review_response = NispReviewResponse.model_validate(latest_review) if latest_review is not None else None
        pending_correction = await db.scalar(
            select(NispCorrectionRequest)
            .where(
                NispCorrectionRequest.registration_id == registration.id,
                NispCorrectionRequest.status == "pending",
            )
            .limit(1)
        )
        versions = (
            await db.execute(
                select(NispRegistrationVersion)
                .where(NispRegistrationVersion.registration_id == registration.id)
                .order_by(NispRegistrationVersion.version_no.desc())
            )
        ).scalars().all()
        final_export_item_id = await db.scalar(
            select(NispExportItem.id)
            .join(NispExportJob, NispExportJob.id == NispExportItem.job_id)
            .where(
                NispExportItem.registration_id == registration.id,
                NispExportItem.is_valid.is_(True),
                NispExportJob.status == "succeeded",
            )
            .order_by(NispExportItem.id.desc())
            .limit(1)
        )
        return NispRegistrationResponse(
            id=registration.id,
            registration_no=registration.registration_no,
            batch_id=registration.batch_id,
            plan_id=registration.plan_id,
            order_id=registration.order_id,
            level=registration.level,
            status=registration.status,
            candidate_snapshot=registration.candidate_snapshot,
            order_status=order.status if order else "",
            price_cents=order.price if order else 0,
            out_trade_no=order.out_trade_no if order else None,
            paid_at=order.paid_at if order else None,
            resubmission_count=registration.resubmission_count,
            rejection_count=registration.rejection_count,
            resubmission_due_at=registration.resubmission_due_at,
            last_reviewed_at=registration.last_reviewed_at,
            approved_at=registration.approved_at,
            latest_review=review_response,
            materials=materials,
            pending_correction=(
                NispCorrectionRequestResponse.model_validate(pending_correction)
                if pending_correction is not None
                else None
            ),
            versions=[
                NispRegistrationVersionResponse.model_validate(row)
                for row in versions
            ],
            final_export_item_id=final_export_item_id,
            created_at=registration.created_at,
            updated_at=registration.updated_at,
        )

    async def _admin_response(
        self, db, registration: NispRegistration
    ) -> NispRegistrationResponse:
        response = await self._response(db, registration)
        rows = (
            await db.execute(
                select(NispMaterialFile)
                .where(NispMaterialFile.registration_id == registration.id)
                .order_by(NispMaterialFile.material_type, NispMaterialFile.version_no)
            )
        ).scalars().all()
        material_responses: list[NispMaterialResponse] = []
        for material in rows:
            preview_url = None
            if material.is_current:
                try:
                    preview_url = await self.storage.signed_get_url(material.storage_key)
                except Exception:
                    preview_url = None
            item = NispMaterialResponse(
                id=material.id,
                material_type=material.material_type,
                version_no=material.version_no,
                storage_key=material.storage_key,
                original_filename=material.original_filename,
                content_type=material.content_type,
                size_bytes=material.size_bytes,
                sha256=material.sha256,
                is_current=material.is_current,
                uploaded_at=material.created_at,
            )
            item.preview_url = preview_url
            material_responses.append(item)
        response.materials = material_responses
        return response

    async def get_admin_registration(
        self, registration_id: int
    ) -> NispRegistrationResponse:
        async with get_db_ctx() as db:
            registration = await db.get(NispRegistration, registration_id)
            if registration is None:
                raise NotFoundException("NISP 报名记录")
            return await self._admin_response(db, registration)
