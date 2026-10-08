"""NISP registration service: order creation, review, export-ready snapshots."""

import re
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.domain.nisp.src import (
    NispExamBatch,
    NispMaterialFile,
    NispRegistration,
    NispReview,
    NispRefundRequest,
)
from app.integrations.nisp_storage import NispObjectStorage
from app.domain.plan.src.index import Plan
from app.port.exceptions import BusinessException, ConflictException, NotFoundException
from app.schemas.nisp import (
    NispOrderCreate,
    NispRegistrationResponse,
    NispReviewResponse,
    NispReviewDecisionRequest,
    NispResubmitRequest,
    NispMaterialResponse,
)
from app.services.points_mall import release_order_coupon


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
            # Agreement check
            await ensure_accepted(
                db,
                user_id,
                "cert_registration",
                message="请先阅读并同意认证报名信息处理授权协议",
            )

            # Lock batch
            batch = (
                await db.execute(
                    select(NispExamBatch)
                    .where(NispExamBatch.id == data.batch_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if batch is None:
                raise NotFoundException("NISP 考试批次")

            plan = await db.get(Plan, batch.plan_id)
            if plan is None or plan.status != "published":
                raise ConflictException("该考试批次未开放报名")

            # Check level consistency
            if data.level != batch.level:
                raise ConflictException("报名级别与批次不匹配")

            # Check duplicate registration
            existing = (
                await db.execute(
                    select(NispRegistration).where(
                        NispRegistration.batch_id == batch.id,
                        NispRegistration.candidate_idcard == data.id_card,
                        NispRegistration.status.in_(
                            ("pending_payment", "pending_review",
                             "rejected_awaiting_resubmission", "approved")
                        ),
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

            # Generate registration number
            seq = (await db.scalar(
                select(func.count()).select_from(NispRegistration)
            )) or 0
            reg_no = _registration_no(data.level, seq + 1)

            # Create order
            expires_at = _now() + timedelta(minutes=batch.payment_timeout_minutes)
            order = Order(
                user_id=user_id,
                order_kind="certification",
                product_type=f"NISP-{data.level}",
                candidate_name=data.name,
                candidate_phone=data.phone,
                candidate_idcard=data.id_card,
                price=price,
                status="pending",
                out_trade_no=generate_out_trade_no("ORD"),
                expires_at=expires_at,
            )
            db.add(order)
            await db.flush()

            # Build snapshot (matching Excel export columns)
            snapshot = self._build_snapshot(data, batch, plan)

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
                registration_no=reg_no,
                level=data.level,
                status="pending_payment",
                candidate_snapshot=snapshot,
                candidate_idcard=data.id_card,
                material_keys=material_keys,
            )
            db.add(registration)
            await db.flush()
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

            from app.domain.order.src.index import Order, apply_order_status_transition

            order = (
                await db.execute(
                    select(Order)
                    .where(Order.id == reg.order_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if order is not None and order.status == "pending":
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
            if reg.status != "rejected_awaiting_resubmission":
                raise ConflictException("当前状态不能补交材料")

            batch = await db.get(NispExamBatch, reg.batch_id)
            if reg.resubmission_due_at and reg.resubmission_due_at < _now():
                raise ConflictException("补交材料已超时")

            # Update provided material keys
            keys = reg.material_keys or {}
            updates = {
                "id_card_both_sides": data.id_card_both_sides_key,
                "portrait_photo": data.portrait_photo_key,
                "xuexin_report": data.xuexin_report_key,
                "application_form": data.application_form_key,
            }
            for k, v in updates.items():
                if v:
                    await self._bind_material(
                        db,
                        user_id=user_id,
                        registration_id=reg.id,
                        material_type=k,
                        storage_key=v,
                        replace_current=True,
                    )
                    keys[k] = v
            reg.material_keys = keys
            reg.resubmission_count += 1
            reg.status = "pending_review"
            reg.resubmission_due_at = None
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
            reg = (
                await db.execute(
                    select(NispRegistration)
                    .where(NispRegistration.id == registration_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if reg is None:
                raise NotFoundException("NISP 报名记录")
            if reg.status != "pending_review":
                raise ConflictException("当前状态不能审核")

            batch = await db.get(NispExamBatch, reg.batch_id)
            now = _now()
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
                else:
                    # Max resubmissions reached — transition to refund flow
                    from app.domain.order.src.index import Order
                    order = await db.get(Order, reg.order_id)
                    reg.status = "pending_refund_confirmation"
                    reg.closed_at = now
                    reg.close_reason = "review_failed_max_resubmissions"
                    if order is not None:
                        db.add(NispRefundRequest(
                            registration_id=reg.id,
                            order_id=reg.order_id,
                            user_id=reg.user_id,
                            request_kind="review_failed",
                            reason_code=data.reason_code or "review_rejected",
                            reason_detail=data.reason_detail,
                            amount_cents=order.price,
                            status="requested",
                            requested_at=now,
                        ))

            await db.commit()
            await db.refresh(reg)
            return await self._response(db, reg)

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
        self, data: NispOrderCreate, batch: NispExamBatch, plan: Plan
    ) -> dict:
        """Build a snapshot matching NISP Excel export columns."""
        snapshot = {
            "name": data.name,
            "pinyin": data.pinyin,
            "major": data.major,
            "school": data.school,
            "id_card": data.id_card,
            "phone": data.phone,
            "email": data.email,
            "province": data.province,
            "training_type": f"NISP{'一级' if data.level == '1' else '二级'}",
            "birth_date": _birth_date_from_idcard(data.id_card),
            "exam_date": plan.exam_date.isoformat() if plan.exam_date else None,
            "exam_location": plan.exam_location,
            "institution": batch.training_org,
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
            latest_review=(
                NispReviewResponse.model_validate(latest_review)
                if latest_review is not None
                else None
            ),
            materials=materials,
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
