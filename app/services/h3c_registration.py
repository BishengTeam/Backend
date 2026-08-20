"""H3C exam-batch registration, review, and material-resubmission workflow."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapter.database import get_db_ctx
from app.domain.certification.src.index import Certification
from app.domain.h3c.src.index import (
    H3C_REGISTRATION_TYPES,
    H3cExamBatch,
    H3cMaterial,
    H3cMaterialUpload,
    H3cRefundRequest,
    H3cRegistration,
    H3cReview,
)
from app.domain.order.src.index import (
    INVENTORY_LOCK_ACTION,
    Order,
    add_inventory_record,
    apply_order_status_transition,
    confirm_inventory_sale,
    lock_inventory,
    release_inventory_lock,
)
from app.domain.plan.src.index import Plan
from app.domain.user.src.index import User, UserRealname
from app.integrations.h3c_storage import H3cObjectStorage, assert_owned_source_key
from app.port.exceptions import BusinessException, ConflictException, NotFoundException, ValidationException
from app.schemas.common import PaginatedData
from app.schemas.h3c_registration import (
    H3cMaterialResponse,
    H3cMaterialUploadResponse,
    H3cOrderCreate,
    H3cProfileDefaults,
    H3cRegistrationResponse,
    H3cResubmissionCreate,
    H3cReviewDecision,
    H3cUserExamBatchResponse,
)
from app.services.plan_enrollment import PlanEnrollmentService
from app.services.user import UserService
from app.utils.payment import generate_out_trade_no


H3C_ACTIVE_STATUSES = (
    "pending_payment",
    "pending_review",
    "rejected_awaiting_resubmission",
    "pending_refund_confirmation",
    "refund_processing",
    "approved",
)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _birth_date_from_idcard(value: str) -> str:
    return f"{value[6:10]}/{value[10:12]}/{value[12:14]}"


def _price(batch: H3cExamBatch, registration_type: str) -> int:
    return {
        "coupon": batch.coupon_price_cents,
        "student": batch.student_price_cents,
        "full": batch.full_price_cents,
    }[registration_type]


class H3cRegistrationService:
    def __init__(self, storage: H3cObjectStorage | None = None) -> None:
        self.storage = storage or H3cObjectStorage()

    async def get_profile_defaults(self, user_id: int) -> H3cProfileDefaults:
        profile = await UserService().get_profile(user_id)
        return H3cProfileDefaults(
            candidate_name=profile.real_name,
            gender=profile.gender,
            candidate_idcard=profile.id_card,
            school=profile.school,
            address=f"{profile.province or ''}{profile.city or ''}{profile.address or ''}".strip() or None,
            phone=profile.phone,
            email=profile.email,
            education=profile.education,
            first_name_en=profile.first_name_en,
            last_name_en=profile.last_name_en,
        )

    async def list_user_batches(self) -> list[H3cUserExamBatchResponse]:
        async with get_db_ctx() as db:
            rows = (
                await db.execute(
                    select(H3cExamBatch, Plan, Certification)
                    .join(Plan, Plan.id == H3cExamBatch.plan_id)
                    .join(Certification, Certification.code == Plan.product_type)
                    .where(
                        Plan.status == "published",
                        Certification.vendor == "H3C",
                        Certification.is_active.is_(True),
                    )
                    .order_by(Plan.sort_order.desc(), Plan.id.desc())
                )
            ).all()
            result: list[H3cUserExamBatchResponse] = []
            for batch, plan, certification in rows:
                occupied = await db.scalar(
                    select(func.count())
                    .select_from(Order)
                    .where(
                        Order.plan_id == plan.id,
                        Order.status.in_(("pending", "paid", "completed")),
                    )
                ) or 0
                remaining = max(0, plan.capacity - int(occupied))
                result.append(
                    H3cUserExamBatchResponse(
                        id=batch.id,
                        plan_id=plan.id,
                        certification_code=certification.code,
                        name=plan.name,
                        status=plan.status,
                        apply_start=plan.apply_start,
                        apply_end=plan.apply_end,
                        exam_date=plan.exam_date,
                        remaining_count=remaining,
                        exam_location=plan.exam_location,
                        description=plan.description,
                        prices=[
                            {"registration_type": kind, "price_cents": _price(batch, kind)}
                            for kind in H3C_REGISTRATION_TYPES
                        ],
                        payment_timeout_minutes=batch.payment_timeout_minutes,
                        max_material_bytes=batch.max_material_bytes,
                    )
                )
            return result

    async def upload_material(
        self,
        *,
        user_id: int,
        batch_id: int,
        material_type: str,
        filename: str,
        content_type: str | None,
        data: bytes,
    ) -> H3cMaterialUploadResponse:
        if material_type not in {"coupon_proof", "student_proof"}:
            raise ValidationException("不支持的 H3C 材料类型")
        batch = await self._get_batch(batch_id)
        key, size, digest = await self.storage.save_source(
            user_id=user_id,
            filename=filename,
            content_type=content_type,
            data=data,
            max_bytes=batch.max_material_bytes,
        )
        async with get_db_ctx() as db:
            async with db.begin():
                db.add(
                    H3cMaterialUpload(
                        user_id=user_id,
                        material_type=material_type,
                        storage_key=key,
                        original_filename=Path(filename).name,
                        size_bytes=size,
                        sha256=digest,
                    )
                )
        return H3cMaterialUploadResponse(
            material_type=material_type,
            storage_key=key,
            size_bytes=size,
            sha256=digest,
        )

    async def create_order(
        self,
        user_id: int,
        data: H3cOrderCreate,
    ) -> H3cRegistrationResponse:
        async with get_db_ctx() as db:
            async with db.begin():
                user = await db.get(User, user_id)
                if user is None or not user.is_active:
                    raise BusinessException("用户不可用")
                identity = await db.scalar(
                    select(UserRealname).where(
                        UserRealname.user_id == user_id,
                        UserRealname.status == "verified",
                    )
                )
                if identity is None:
                    raise BusinessException("请先完成实名认证")

                batch = await self._get_batch_for_update(db, data.batch_id)
                active_registration_id = await db.scalar(
                    select(H3cRegistration.id)
                    .where(
                        H3cRegistration.batch_id == batch.id,
                        H3cRegistration.candidate_idcard == data.candidate_idcard,
                        H3cRegistration.status.in_(H3C_ACTIVE_STATUSES),
                    )
                    .limit(1)
                )
                if active_registration_id is not None:
                    raise ConflictException("该身份证号已报名此考试批次")
                plan = await PlanEnrollmentService().lock_enrollable_plan(
                    db,
                    plan_id=batch.plan_id,
                    user_id=user_id,
                    expected_vendor="H3C",
                )

                requested_keys = {
                    key: value
                    for key, value in {
                        "coupon_proof": data.coupon_proof_key,
                        "student_proof": data.student_proof_key,
                    }.items()
                    if value
                }
                if data.registration_type == "coupon" and set(requested_keys) != {"coupon_proof"}:
                    raise ValidationException("考券报名必须且只能上传优惠券证明图片")
                if data.registration_type == "student" and set(requested_keys) != {"student_proof"}:
                    raise ValidationException("学生报名必须且只能上传学生证明图片")
                if data.registration_type == "full" and requested_keys:
                    raise ValidationException("全额报名不能上传 H3C 证明材料")
                material_uploads = await self._material_uploads(
                    db,
                    user_id=user_id,
                    keys=requested_keys,
                )
                price_cents = _price(batch, data.registration_type)
                inventory_change = await lock_inventory(
                    db,
                    inventory_type="h3c_batch",
                    ref_code=f"h3c-batch-{batch.id}",
                )
                now = _now()
                order = Order(
                    user_id=user_id,
                    order_kind="certification",
                    product_type=plan.product_type,
                    plan_id=plan.id,
                    inventory_id=inventory_change.inventory_id,
                    candidate_name=data.candidate_name,
                    candidate_phone=data.phone,
                    candidate_idcard=data.candidate_idcard,
                    price=price_cents,
                    status="pending",
                    out_trade_no=generate_out_trade_no("H3C"),
                    expires_at=now + timedelta(
                        minutes=batch.payment_timeout_minutes
                    ),
                    attachments=list(requested_keys.values()),
                )
                db.add(order)
                await db.flush()
                add_inventory_record(
                    db,
                    change=inventory_change,
                    order_id=order.id,
                    action=INVENTORY_LOCK_ACTION,
                    reason="h3c_order_created",
                )

                registration = H3cRegistration(
                    batch_id=batch.id,
                    plan_id=plan.id,
                    user_id=user_id,
                    order_id=order.id,
                    registration_no="PENDING",
                    registration_type=data.registration_type,
                    status="pending_payment",
                    candidate_snapshot=self._candidate_snapshot(
                        data=data,
                        batch=batch,
                        plan=plan,
                    ),
                    candidate_idcard=data.candidate_idcard,
                )
                db.add(registration)
                await db.flush()
                registration.registration_no = (
                    f"H3C-{data.registration_type.upper()}-{registration.id:08d}"
                )
                for material_type, upload in material_uploads.items():
                    db.add(
                        H3cMaterial(
                            registration_id=registration.id,
                            material_type=material_type,
                            version_no=1,
                            storage_key=upload.storage_key,
                            original_filename=upload.original_filename,
                            size_bytes=upload.size_bytes,
                            sha256=upload.sha256,
                            uploaded_at=now,
                        )
                    )

                if price_cents == 0:
                    apply_order_status_transition(order, "completed")
                    order.paid_at = now
                    order.expires_at = None
                    await confirm_inventory_sale(db, order, reason="h3c_zero_price_order")
                    registration.status = "pending_review"
                await db.flush()
            return await self.get_registration(user_id, registration.id)

    @staticmethod
    def _validate_material_ownership(
        *,
        user_id: int,
        registration_type: str,
        coupon_proof_key: str | None,
        student_proof_key: str | None,
    ) -> dict[str, str]:
        result: dict[str, str] = {}
        if registration_type == "coupon" and coupon_proof_key:
            assert_owned_source_key(user_id, coupon_proof_key)
            result["coupon_proof"] = coupon_proof_key
        if registration_type == "student" and student_proof_key:
            assert_owned_source_key(user_id, student_proof_key)
            result["student_proof"] = student_proof_key
        return result

    @staticmethod
    def _candidate_snapshot(
        *,
        data: H3cOrderCreate,
        batch: H3cExamBatch,
        plan: Plan,
    ) -> dict:
        return {
            "candidate_name": data.candidate_name,
            "gender": data.gender,
            "candidate_idcard": data.candidate_idcard,
            "school": data.school,
            "address": data.address,
            "phone": data.phone,
            "email": data.email,
            "education": data.education,
            "first_name_en": data.first_name_en,
            "last_name_en": data.last_name_en,
            "coupon_code": data.coupon_code,
            "verify_code": data.verify_code,
            "birth_date": _birth_date_from_idcard(data.candidate_idcard),
            "candidate_no": None,
            "exam_code": batch.exam_code,
            "exam_datetime": plan.exam_date.isoformat() if plan.exam_date else None,
            "country": batch.country,
            "language": batch.language,
            "identity_tag": batch.identity_tag,
            "training_org": batch.training_org,
            "training_teacher": batch.training_teacher,
            "training_address": batch.training_address,
            "training_start": batch.training_start.isoformat() if batch.training_start else None,
            "training_end": batch.training_end.isoformat() if batch.training_end else None,
        }

    async def list_registrations(
        self,
        user_id: int,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedData[H3cRegistrationResponse]:
        async with get_db_ctx() as db:
            base = select(H3cRegistration).where(H3cRegistration.user_id == user_id)
            total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
            rows = (
                await db.execute(
                    base.order_by(H3cRegistration.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            return PaginatedData(
                items=[await self._response(db, row) for row in rows],
                total=int(total),
                page=page,
                page_size=page_size,
            )

    async def list_admin_registrations(
        self,
        *,
        batch_id: int | None = None,
        registration_type: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedData[H3cRegistrationResponse]:
        async with get_db_ctx() as db:
            base = select(H3cRegistration)
            if batch_id is not None:
                base = base.where(H3cRegistration.batch_id == batch_id)
            if registration_type:
                base = base.where(H3cRegistration.registration_type == registration_type)
            if status:
                base = base.where(H3cRegistration.status == status)
            total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
            rows = (
                await db.execute(
                    base.order_by(H3cRegistration.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            return PaginatedData(
                items=[await self._response(db, row) for row in rows],
                total=int(total),
                page=page,
                page_size=page_size,
            )

    async def get_registration(
        self,
        user_id: int,
        registration_id: int,
    ) -> H3cRegistrationResponse:
        async with get_db_ctx() as db:
            registration = await self._get_user_registration(db, user_id, registration_id)
            return await self._response(db, registration)

    async def cancel_pending_payment(self, user_id: int, registration_id: int) -> H3cRegistrationResponse:
        async with get_db_ctx() as db:
            async with db.begin():
                registration = await db.scalar(
                    select(H3cRegistration)
                    .where(
                        H3cRegistration.id == registration_id,
                        H3cRegistration.user_id == user_id,
                    )
                    .with_for_update()
                )
                if registration is None:
                    raise NotFoundException("H3C 报名")
                if registration.status != "pending_payment":
                    raise ConflictException("仅待支付报名可以取消")
                order = await db.scalar(
                    select(Order).where(Order.id == registration.order_id).with_for_update()
                )
                if order is None or order.status != "pending":
                    raise ConflictException("订单状态不允许取消")
                apply_order_status_transition(order, "closed")
                order.closed_at = _now()
                order.close_reason = "user_cancelled"
                await release_inventory_lock(db, order, reason="user_cancelled")
                registration.status = "cancelled"
                registration.closed_at = order.closed_at
                registration.close_reason = "user_cancelled"
            return await self.get_registration(user_id, registration_id)

    async def resubmit_materials(
        self,
        user_id: int,
        registration_id: int,
        data: H3cResubmissionCreate,
    ) -> H3cRegistrationResponse:
        data = H3cResubmissionCreate.model_validate(data)
        async with get_db_ctx() as db:
            async with db.begin():
                registration = await db.scalar(
                    select(H3cRegistration)
                    .where(
                        H3cRegistration.id == registration_id,
                        H3cRegistration.user_id == user_id,
                    )
                    .with_for_update()
                )
                if registration is None:
                    raise NotFoundException("H3C 报名")
                if registration.status != "rejected_awaiting_resubmission":
                    raise ConflictException("当前报名状态不能补交材料")
                batch = await self._get_batch_for_update(db, registration.batch_id)
                now = _now()
                if registration.resubmission_count >= batch.max_resubmissions:
                    raise ConflictException("补交次数已用完")
                due_at = _utc(registration.resubmission_due_at)
                if due_at is not None and now > due_at:
                    raise ConflictException("补交材料已超时，请等待退款处理")

                latest_review = await db.scalar(
                    select(H3cReview)
                    .where(H3cReview.registration_id == registration.id)
                    .order_by(H3cReview.id.desc())
                    .limit(1)
                )
                if latest_review is None or not latest_review.rejected_material_types:
                    raise ConflictException("未找到可补交的材料项")
                allowed = set(latest_review.rejected_material_types)
                submitted = {
                    key: value
                    for key, value in {
                        "coupon_proof": data.coupon_proof_key,
                        "student_proof": data.student_proof_key,
                    }.items()
                    if value
                }
                if set(submitted) != allowed:
                    raise ValidationException("只能重新上传被拒绝的材料项")
                for key, value in submitted.items():
                    upload_rows = await self._material_uploads(
                        db,
                        user_id=user_id,
                        keys={key: value},
                    )
                    upload = upload_rows[key]
                    old_rows = (
                        await db.execute(
                            select(H3cMaterial)
                            .where(
                                H3cMaterial.registration_id == registration.id,
                                H3cMaterial.material_type == key,
                                H3cMaterial.is_current.is_(True),
                            )
                            .with_for_update()
                        )
                    ).scalars().all()
                    for old in old_rows:
                        old.is_current = False
                    next_version = 1 + len(
                        (
                            await db.execute(
                                select(H3cMaterial.id).where(
                                    H3cMaterial.registration_id == registration.id,
                                    H3cMaterial.material_type == key,
                                )
                            )
                        ).scalars().all()
                    )
                    db.add(
                        H3cMaterial(
                            registration_id=registration.id,
                            material_type=key,
                            version_no=next_version,
                            storage_key=upload.storage_key,
                            original_filename=upload.original_filename,
                            size_bytes=upload.size_bytes,
                            sha256=upload.sha256,
                            uploaded_at=now,
                        )
                    )
                registration.resubmission_count += 1
                registration.resubmission_due_at = None
                registration.status = "pending_review"
            return await self.get_registration(user_id, registration_id)

    async def review(
        self,
        *,
        admin_id: int,
        registration_id: int,
        decision_data: H3cReviewDecision,
    ) -> H3cRegistrationResponse:
        decision_data = H3cReviewDecision.model_validate(decision_data)
        async with get_db_ctx() as db:
            async with db.begin():
                registration = await db.scalar(
                    select(H3cRegistration)
                    .where(H3cRegistration.id == registration_id)
                    .with_for_update()
                )
                if registration is None:
                    raise NotFoundException("H3C 报名")
                if registration.status != "pending_review":
                    raise ConflictException("当前报名状态不能审核")
                batch = await self._get_batch_for_update(db, registration.batch_id)
                now = _now()
                current_material_ids = (
                    await db.execute(
                        select(H3cMaterial.id).where(
                            H3cMaterial.registration_id == registration.id,
                            H3cMaterial.is_current.is_(True),
                        )
                    )
                ).scalars().all()
                review = H3cReview(
                    registration_id=registration.id,
                    decision=decision_data.decision,
                    reason_code=decision_data.reason_code,
                    reason_detail=decision_data.reason_detail,
                    rejected_material_types=decision_data.rejected_material_types or None,
                    material_version_ids=[int(value) for value in current_material_ids],
                    reviewer_admin_id=admin_id,
                    reviewed_at=now,
                )
                db.add(review)
                registration.last_reviewed_at = now
                if decision_data.decision == "approved":
                    registration.status = "approved"
                    registration.approved_at = now
                    registration.resubmission_due_at = None
                else:
                    registration.rejection_count += 1
                    if registration.resubmission_count < batch.max_resubmissions:
                        registration.status = "rejected_awaiting_resubmission"
                        registration.resubmission_due_at = now + timedelta(
                            hours=batch.resubmission_window_hours
                        )
                    else:
                        registration.status = "pending_refund_confirmation"
                        registration.resubmission_due_at = None
                        await self._create_refund_request(
                            db,
                            registration=registration,
                            request_kind="review_failed",
                            reason_code=decision_data.reason_code or "review_rejected",
                            reason_detail=decision_data.reason_detail,
                            admin_id=None,
                        )
            return await self._admin_registration(registration_id)

    async def close_registration(
        self,
        *,
        admin_id: int,
        registration_id: int,
        reason: str,
    ) -> H3cRegistrationResponse:
        async with get_db_ctx() as db:
            async with db.begin():
                registration = await db.scalar(
                    select(H3cRegistration)
                    .where(H3cRegistration.id == registration_id)
                    .with_for_update()
                )
                if registration is None:
                    raise NotFoundException("H3C 报名")
                if registration.status in {"cancelled", "refunded_closed", "refund_processing"}:
                    raise ConflictException("当前 H3C 报名状态不能关闭")
                order = await db.scalar(
                    select(Order).where(Order.id == registration.order_id).with_for_update()
                )
                if order is None:
                    raise ConflictException("H3C 报名缺少订单")
                now = _now()
                if order.status == "pending":
                    apply_order_status_transition(order, "closed")
                    order.closed_at = now
                    order.close_reason = "admin_closed"
                    await release_inventory_lock(db, order, reason="admin_closed")
                    registration.status = "cancelled"
                else:
                    registration.status = "pending_refund_confirmation"
                    await self._create_refund_request(
                        db,
                        registration=registration,
                        request_kind="exception_close",
                        reason_code="admin_exception_close",
                        reason_detail=reason,
                        admin_id=admin_id,
                    )
                registration.resubmission_due_at = None
                registration.closed_at = now
                registration.close_reason = "admin_closed"
            return await self._admin_registration(registration_id)

    async def _admin_registration(self, registration_id: int) -> H3cRegistrationResponse:
        async with get_db_ctx() as db:
            registration = await db.get(H3cRegistration, registration_id)
            if registration is None:
                raise NotFoundException("H3C 报名")
            return await self._response(db, registration)

    async def process_resubmission_timeouts(self, *, limit: int = 100) -> int:
        now = _now()
        processed = 0
        async with get_db_ctx() as db:
            rows = (
                await db.execute(
                    select(H3cRegistration)
                    .where(
                        H3cRegistration.status == "rejected_awaiting_resubmission",
                        H3cRegistration.resubmission_due_at.is_not(None),
                        H3cRegistration.resubmission_due_at <= now,
                    )
                    .order_by(H3cRegistration.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).scalars().all()
            for registration in rows:
                registration.status = "pending_refund_confirmation"
                registration.resubmission_due_at = None
                await self._create_refund_request(
                    db,
                    registration=registration,
                    request_kind="review_failed",
                    reason_code="resubmission_timeout",
                    reason_detail="用户超时未重新提交 H3C 证明材料",
                    admin_id=None,
                )
                processed += 1
            await db.commit()
        return processed

    @staticmethod
    async def _material_uploads(
        db: AsyncSession,
        *,
        user_id: int,
        keys: dict[str, str | None],
    ) -> dict[str, H3cMaterialUpload]:
        cleaned = {key: value for key, value in keys.items() if value}
        if not cleaned:
            return {}
        rows = (
            await db.execute(
                select(H3cMaterialUpload).where(
                    H3cMaterialUpload.user_id == user_id,
                    H3cMaterialUpload.storage_key.in_(cleaned.values()),
                )
            )
        ).scalars().all()
        by_key = {row.storage_key: row for row in rows}
        result: dict[str, H3cMaterialUpload] = {}
        for material_type, key in cleaned.items():
            assert_owned_source_key(user_id, key)
            row = by_key.get(key)
            if row is None:
                raise ValidationException("H3C 材料不存在，请重新上传")
            if row.material_type != material_type:
                raise ValidationException("H3C 材料类型不匹配")
            result[material_type] = row
        if len(result) != len(cleaned):
            raise ValidationException("H3C 材料不存在，请重新上传")
        return result

    @staticmethod
    async def _create_refund_request(
        db: AsyncSession,
        *,
        registration: H3cRegistration,
        request_kind: str,
        reason_code: str,
        reason_detail: str | None,
        admin_id: int | None,
    ) -> H3cRefundRequest:
        existing_id = await db.scalar(
            select(H3cRefundRequest.id)
            .where(
                H3cRefundRequest.registration_id == registration.id,
                H3cRefundRequest.status.in_(("requested", "approved", "processing", "failed")),
            )
            .limit(1)
        )
        if existing_id is not None:
            return await db.get(H3cRefundRequest, existing_id)
        order = await db.get(Order, registration.order_id)
        if order is None:
            raise ConflictException("H3C 报名缺少订单")
        refund = H3cRefundRequest(
            registration_id=registration.id,
            order_id=registration.order_id,
            user_id=registration.user_id,
            request_kind=request_kind,
            reason_code=reason_code,
            reason_detail=reason_detail,
            amount_cents=order.price,
            requested_by_admin_id=admin_id,
            requested_at=_now(),
        )
        db.add(refund)
        return refund

    async def on_order_paid(self, db: AsyncSession, order: Order) -> bool:
        registration = await db.scalar(
            select(H3cRegistration)
            .where(H3cRegistration.order_id == order.id)
            .with_for_update()
        )
        if registration is None or registration.status != "pending_payment":
            return False
        registration.status = "pending_review"
        return True

    async def on_order_closed(self, db: AsyncSession, order: Order) -> bool:
        registration = await db.scalar(
            select(H3cRegistration)
            .where(H3cRegistration.order_id == order.id)
            .with_for_update()
        )
        if registration is None or registration.status != "pending_payment":
            return False
        registration.status = "cancelled"
        registration.closed_at = order.closed_at or _now()
        registration.close_reason = order.close_reason or "order_closed"
        return True

    async def _response(
        self,
        db: AsyncSession,
        registration: H3cRegistration,
    ) -> H3cRegistrationResponse:
        order = await db.get(Order, registration.order_id)
        materials = (
            await db.execute(
                select(H3cMaterial)
                .where(H3cMaterial.registration_id == registration.id)
                .order_by(H3cMaterial.material_type, H3cMaterial.version_no)
            )
        ).scalars().all()
        latest_review = await db.scalar(
            select(H3cReview)
            .where(H3cReview.registration_id == registration.id)
            .order_by(H3cReview.id.desc())
            .limit(1)
        )

        async def preview(material: H3cMaterial) -> str | None:
            if not material.is_current:
                return None
            try:
                return await self.storage.signed_get_url(material.storage_key)
            except Exception:
                return None

        material_responses = [
            H3cMaterialResponse(
                id=material.id,
                material_type=material.material_type,
                version_no=material.version_no,
                storage_key=material.storage_key,
                preview_url=await preview(material),
                original_filename=material.original_filename,
                size_bytes=material.size_bytes,
                sha256=material.sha256,
                is_current=material.is_current,
                uploaded_at=material.uploaded_at,
            )
            for material in materials
        ]
        return H3cRegistrationResponse(
            id=registration.id,
            registration_no=registration.registration_no,
            batch_id=registration.batch_id,
            plan_id=registration.plan_id,
            order_id=registration.order_id,
            registration_type=registration.registration_type,
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
            materials=material_responses,
            latest_review=(
                H3cReviewResponse.model_validate(latest_review)
                if latest_review is not None
                else None
            ),
            created_at=registration.created_at,
            updated_at=registration.updated_at,
        )

    async def _get_batch(self, batch_id: int) -> H3cExamBatch:
        async with get_db_ctx() as db:
            return await self._get_batch_for_update(db, batch_id, for_update=False)

    @staticmethod
    async def _get_batch_for_update(
        db: AsyncSession,
        batch_id: int,
        *,
        for_update: bool = True,
    ) -> H3cExamBatch:
        stmt = select(H3cExamBatch).where(H3cExamBatch.id == batch_id)
        if for_update:
            stmt = stmt.with_for_update()
        batch = await db.scalar(stmt)
        if batch is None:
            raise NotFoundException("H3C 考试批次")
        return batch

    @staticmethod
    async def _get_user_registration(
        db: AsyncSession,
        user_id: int,
        registration_id: int,
    ) -> H3cRegistration:
        registration = await db.scalar(
            select(H3cRegistration).where(
                H3cRegistration.id == registration_id,
                H3cRegistration.user_id == user_id,
            )
        )
        if registration is None:
            raise NotFoundException("H3C 报名")
        return registration


async def h3c_registration_worker_loop(
    service: H3cRegistrationService | None = None,
) -> None:
    active_service = service or H3cRegistrationService()
    while True:
        try:
            await active_service.process_resubmission_timeouts()
        except Exception:
            await asyncio.sleep(5)
        await asyncio.sleep(30)
