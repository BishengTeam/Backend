"""NISP admin service: batch management and registration review."""

from datetime import datetime, timezone

from sqlalchemy import func, or_, select

from app.adapter.database import get_db_ctx
from app.domain.nisp.src import NispExamBatch, NispExportItem, NispExportJob, NispRegistration
from app.domain.plan.src.index import Plan
from app.port.exceptions import BusinessException, ConflictException, NotFoundException
from app.domain.order.src.index import Order, apply_order_status_transition
from app.models.cert_product import CertProduct
from app.models.consultation_group import CertConsultationGroup
from app.services.nisp_lifecycle import occupied_count, request_refund
from app.services.points_mall import release_order_coupon
from app.services.certification_identity import validate_nisp_batch_product
from app.schemas.nisp import (
    NispExamBatchCreate,
    NispExamBatchFullCreate,
    NispExamBatchResponse,
    NispExamBatchUpdate,
    NispRegistrationResponse,
    NispReviewDecisionRequest,
)
from app.services.nisp_registration import NispRegistrationService


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class NispAdminService:

    async def create_full_batch(
        self, data: NispExamBatchFullCreate
    ) -> NispExamBatchResponse:
        """Create Plan + level-specific NISP batch atomically."""
        async with get_db_ctx() as db:
            async with db.begin():
                product = await db.scalar(
                    select(CertProduct)
                    .where(
                        CertProduct.code == f"NISP-{data.level}",
                        CertProduct.type == "nisp",
                        CertProduct.is_active.is_(True),
                    )
                    .with_for_update()
                )
                if product is None:
                    raise BusinessException(
                        f"NISP {'一级' if data.level == '1' else '二级'}认证产品不存在或未上架"
                    )

                duplicate = await db.scalar(
                    select(Plan.id).where(
                        Plan.product_type == product.code,
                        Plan.name == data.name,
                    )
                )
                if duplicate is not None:
                    raise ConflictException("同名 NISP 考试批次已存在")

                selected_price_field = (
                    "level1_price_cents" if data.level == "1" else "level2_price_cents"
                )
                other_price_field = (
                    "level2_price_cents" if data.level == "1" else "level1_price_cents"
                )
                batch_values = {
                    selected_price_field: data.price_cents,
                    other_price_field: 0,
                }
                plan = Plan(
                    product_type=product.code,
                    name=data.name,
                    apply_start=_utc(data.apply_start),
                    apply_end=_utc(data.apply_end),
                    exam_date=_utc(data.exam_date),
                    capacity=data.capacity,
                    price_cents=data.price_cents,
                    exam_location=data.exam_location,
                    status="published" if data.publish else "draft",
                    published_at=_now() if data.publish else None,
                )
                if data.publish:
                    self._validate_schedule(plan, require_window=True, now=_now())
                db.add(plan)
                await db.flush()

                batch = NispExamBatch(
                    plan_id=plan.id,
                    level=data.level,
                    training_org=data.training_org,
                    consultation_group_qrcode_url=data.consultation_group_qrcode_url,
                    payment_timeout_minutes=data.payment_timeout_minutes,
                    resubmission_window_hours=data.resubmission_window_hours,
                    max_resubmissions=data.max_resubmissions,
                    **batch_values,
                )
                db.add(batch)
                await db.flush()
                await self._sync_legacy_qrcode(
                    db,
                    plan_id=plan.id,
                    qrcode_url=data.consultation_group_qrcode_url,
                )
                return await self._response(db, batch)

    async def create_batch(self, data: NispExamBatchCreate) -> NispExamBatchResponse:
        async with get_db_ctx() as db:
            plan = await db.scalar(
                select(Plan).where(Plan.id == data.plan_id).with_for_update()
            )
            if plan is None:
                raise NotFoundException("Plan")
            await validate_nisp_batch_product(
                db, plan=plan, level=data.level, require_active=True
            )
            batch = NispExamBatch(**data.model_dump())
            db.add(batch)
            await db.commit()
            await db.refresh(batch)
            await self._sync_legacy_qrcode(
                db,
                plan_id=batch.plan_id,
                qrcode_url=data.consultation_group_qrcode_url,
            )
            await db.commit()
            return await self._response(db, batch)

    async def get_batch(self, batch_id: int) -> NispExamBatchResponse:
        async with get_db_ctx() as db:
            batch = await db.get(NispExamBatch, batch_id)
            if batch is None:
                raise NotFoundException("NISP 考试批次")
            return await self._response(db, batch)

    async def update_batch(
        self, batch_id: int, data: NispExamBatchUpdate
    ) -> NispExamBatchResponse:
        async with get_db_ctx() as db:
            async with db.begin():
                batch, plan = await self._lock_batch(db, batch_id)
                await validate_nisp_batch_product(
                    db, plan=plan, level=batch.level, require_active=False
                )
                payload = data.model_dump(exclude_unset=True)
                draft_only_fields = {
                    "name", "apply_start", "apply_end", "exam_date", "exam_location",
                    "capacity", "price_cents", "payment_timeout_minutes",
                    "resubmission_window_hours", "max_resubmissions",
                    "level1_price_cents", "level2_price_cents",
                }
                if plan.status != "draft" and draft_only_fields.intersection(payload):
                    raise ConflictException("仅草稿批次允许编辑完整批次信息")

                if "name" in payload:
                    duplicate = await db.scalar(
                        select(Plan.id).where(
                            Plan.product_type == plan.product_type,
                            Plan.name == payload["name"],
                            Plan.id != plan.id,
                        )
                    )
                    if duplicate is not None:
                        raise ConflictException("同名 NISP 考试批次已存在")
                    plan.name = payload["name"]

                plan_fields = {
                    "apply_start", "apply_end", "exam_date", "exam_location", "capacity"
                }
                for field in plan_fields.intersection(payload):
                    value = (
                        payload[field]
                        if field in {"exam_location", "capacity"}
                        else _utc(payload[field])
                    )
                    setattr(plan, field, value)

                batch_fields = {
                    "training_org", "training_teacher", "training_address",
                    "training_start", "training_end",
                    "payment_timeout_minutes", "resubmission_window_hours",
                    "max_resubmissions",
                }
                for field in batch_fields.intersection(payload):
                    value = payload[field]
                    if field in {"training_start", "training_end"}:
                        value = _utc(value)
                    setattr(batch, field, value)

                if "consultation_group_qrcode_url" in payload:
                    batch.consultation_group_qrcode_url = payload[
                        "consultation_group_qrcode_url"
                    ]
                    await self._sync_legacy_qrcode(
                        db,
                        plan_id=plan.id,
                        qrcode_url=payload["consultation_group_qrcode_url"],
                    )

                if "price_cents" in payload:
                    plan.price_cents = payload["price_cents"]
                    if batch.level == "1":
                        batch.level1_price_cents = payload["price_cents"]
                    else:
                        batch.level2_price_cents = payload["price_cents"]

                if plan_fields.intersection(payload) or "price_cents" in payload:
                    self._validate_schedule(plan)
                if "level1_price_cents" in payload:
                    batch.level1_price_cents = payload["level1_price_cents"]
                    if batch.level == "1":
                        plan.price_cents = payload["level1_price_cents"]
                if "level2_price_cents" in payload:
                    batch.level2_price_cents = payload["level2_price_cents"]
                    if batch.level == "2":
                        plan.price_cents = payload["level2_price_cents"]
                await db.flush()
                await db.refresh(batch)
                return await self._response(db, batch)

    async def list_batches(
        self,
        *,
        keyword: str | None = None,
        level: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[NispExamBatchResponse], int]:
        async with get_db_ctx() as db:
            stmt = select(NispExamBatch).join(Plan, Plan.id == NispExamBatch.plan_id)
            if keyword:
                stmt = stmt.where(Plan.name.ilike(f"%{keyword.strip()}%"))
            if level in {"1", "2"}:
                stmt = stmt.where(NispExamBatch.level == level)
            if status:
                stmt = stmt.where(Plan.status == status)
            total = await db.scalar(
                select(func.count()).select_from(stmt.subquery())
            ) or 0
            rows = (
                await db.execute(
                    stmt
                    .order_by(NispExamBatch.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            return [await self._response(db, row) for row in rows], total

    async def publish_batch(self, batch_id: int) -> NispExamBatchResponse:
        async with get_db_ctx() as db:
            plan_id = await db.scalar(
                select(NispExamBatch.plan_id).where(NispExamBatch.id == batch_id)
            )
            if plan_id is None:
                raise NotFoundException("NISP 考试批次")
            plan = await db.scalar(
                select(Plan).where(Plan.id == plan_id).with_for_update()
            )
            if plan is None:
                raise NotFoundException("Plan")
            batch = await db.scalar(
                select(NispExamBatch)
                .where(NispExamBatch.id == batch_id)
                .with_for_update()
            )
            if batch is None:
                raise NotFoundException("NISP 考试批次")
            await validate_nisp_batch_product(
                db, plan=plan, level=batch.level, require_active=True
            )
            self._validate_schedule(plan, require_window=True, now=_now())
            if plan.status == "draft":
                plan.status = "published"
                plan.published_at = _now()
            await db.commit()
            await db.refresh(batch)
            return await self._response(db, batch)

    async def close_registration(self, batch_id: int) -> NispExamBatchResponse:
        async with get_db_ctx() as db:
            plan_id = await db.scalar(
                select(NispExamBatch.plan_id).where(NispExamBatch.id == batch_id)
            )
            if plan_id is None:
                raise NotFoundException("NISP 考试批次")
            plan = await db.scalar(
                select(Plan).where(Plan.id == plan_id).with_for_update()
            )
            if plan is None:
                raise NotFoundException("Plan")
            batch = await db.scalar(
                select(NispExamBatch)
                .where(NispExamBatch.id == batch_id)
                .with_for_update()
            )
            if batch is None or batch.plan_id != plan.id:
                raise NotFoundException("NISP 考试批次")
            if plan.status not in {"published", "registration_closed"}:
                raise ConflictException("当前批次不能关闭报名")
            plan.status = "registration_closed"
            plan.registration_closed_at = _now()
            await db.commit()
            await db.refresh(batch)
            return await self._response(db, batch)

    async def cancel_batch(self, batch_id: int) -> NispExamBatchResponse:
        async with get_db_ctx() as db:
            plan_id = await db.scalar(
                select(NispExamBatch.plan_id).where(NispExamBatch.id == batch_id)
            )
            if plan_id is None:
                raise NotFoundException("NISP 考试批次")
            plan = await db.scalar(
                select(Plan).where(Plan.id == plan_id).with_for_update()
            )
            if plan is None:
                raise NotFoundException("Plan")
            batch = await db.scalar(
                select(NispExamBatch)
                .where(NispExamBatch.id == batch_id)
                .with_for_update()
            )
            if batch is None or batch.plan_id != plan.id:
                raise NotFoundException("NISP 考试批次")
            if plan.status not in {"published", "registration_closed", "cancelled"}:
                raise ConflictException("当前批次不能取消")
            # Re-running cancellation also repairs batches cancelled by the
            # previous implementation, which only changed the plan status.
            registrations = (await db.execute(select(
                NispRegistration.id, NispRegistration.order_id
            ).where(NispRegistration.batch_id == batch.id).order_by(NispRegistration.order_id))).all()
            for registration_id, order_id in registrations:
                # Payment and refund callbacks lock order before registration.
                order = await db.scalar(select(Order).where(Order.id == order_id).with_for_update())
                reg = await db.scalar(select(NispRegistration).where(
                    NispRegistration.id == registration_id
                ).with_for_update())
                if reg.status in {"cancelled", "refunded_closed"}:
                    continue
                if order is None:
                    raise ConflictException("NISP 报名缺少订单")
                if order.status == "pending":
                    from app.services.offline_payment import protected_payment
                    if protected_payment(order):
                        order.offline_status = "exception"
                        order.expires_at = None
                        continue
                    await release_order_coupon(db, order_id=order.id,
                                               user_id=order.user_id, coupon_code=order.coupon_code)
                    apply_order_status_transition(order, "closed")
                    order.closed_at = _now()
                    order.close_reason = "batch_cancelled"
                    reg.status = "cancelled"
                    reg.closed_at = order.closed_at
                    reg.close_reason = "batch_cancelled"
                    reg.resubmission_due_at = None
                elif order.status in {"paid", "completed"}:
                    await request_refund(db, reg, order, request_kind="batch_cancelled",
                                         reason_code="batch_cancelled", reason_detail="考试批次已取消")
            plan.status = "cancelled"
            plan.cancelled_at = _now()
            await db.commit()
            await db.refresh(batch)
            return await self._response(db, batch)

    async def list_registrations(
        self,
        *,
        batch_id: int | None = None,
        level: str | None = None,
        status: str | None = None,
        keyword: str | None = None,
        has_valid_export: bool | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[NispRegistrationResponse], int]:
        service = NispRegistrationService()
        async with get_db_ctx() as db:
            base = select(NispRegistration)
            if batch_id is not None:
                base = base.where(NispRegistration.batch_id == batch_id)
            if level is not None:
                base = base.where(NispRegistration.level == level)
            if status is not None:
                base = base.where(NispRegistration.status == status)
            if keyword:
                pattern = f"%{keyword.strip()}%"
                base = base.where(
                    or_(
                        NispRegistration.registration_no.ilike(pattern),
                        NispRegistration.candidate_snapshot["name"].astext.ilike(pattern),
                    )
                )
            if has_valid_export is not None:
                valid_export_exists = (
                    select(NispExportItem.id)
                    .join(NispExportJob, NispExportJob.id == NispExportItem.job_id)
                    .where(
                        NispExportItem.registration_id == NispRegistration.id,
                        NispExportItem.is_valid.is_(True),
                        or_(
                            NispExportJob.status == "succeeded",
                            (
                                (NispExportJob.status == "failed")
                                & (NispExportJob.last_error == "expired")
                                & NispExportJob.storage_key.is_(None)
                            ),
                        ),
                    )
                    .exists()
                )
                base = base.where(
                    valid_export_exists if has_valid_export else ~valid_export_exists
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
            return [await service._response(db, r) for r in rows], total

    async def review(
        self,
        *,
        admin_id: int,
        registration_id: int,
        data: NispReviewDecisionRequest,
    ) -> NispRegistrationResponse:
        return await NispRegistrationService().review(
            admin_id=admin_id,
            registration_id=registration_id,
            data=data,
        )

    async def _lock_batch(self, db, batch_id: int) -> tuple[NispExamBatch, Plan]:
        batch = await db.scalar(
            select(NispExamBatch)
            .where(NispExamBatch.id == batch_id)
            .with_for_update()
        )
        if batch is None:
            raise NotFoundException("NISP 考试批次")
        plan = await db.scalar(
            select(Plan).where(Plan.id == batch.plan_id).with_for_update()
        )
        if plan is None:
            raise NotFoundException("Plan")
        return batch, plan

    @staticmethod
    async def _sync_legacy_qrcode(db, *, plan_id: int, qrcode_url: str | None) -> None:
        """Keep legacy NISP clients compatible with the shared configuration."""
        row = await db.scalar(
            select(CertConsultationGroup)
            .where(CertConsultationGroup.plan_id == plan_id)
            .with_for_update()
        )
        cleaned = (qrcode_url or "").strip() or None
        if cleaned is None:
            if row is not None:
                await db.delete(row)
            return
        if row is None:
            db.add(CertConsultationGroup(
                plan_id=plan_id,
                qrcode_url=cleaned,
                version=1,
            ))
        else:
            row.qrcode_url = cleaned
            row.version += 1

    @staticmethod
    def _validate_schedule(
        plan: Plan,
        *,
        require_window: bool = False,
        now: datetime | None = None,
    ) -> None:
        start = _utc(plan.apply_start)
        end = _utc(plan.apply_end)
        exam = _utc(plan.exam_date)
        if require_window and (start is None or end is None or exam is None):
            raise BusinessException("发布前必须配置报名开始、截止时间和考试时间")
        if start is not None and end is not None and start >= end:
            raise BusinessException("报名开始时间必须早于截止时间")
        if exam is not None and end is not None and exam < end:
            raise BusinessException("考试时间不能早于报名截止时间")
        if require_window and end is not None and end <= (now or _now()):
            raise BusinessException("报名截止时间必须晚于当前时间")

    async def _response(self, db, batch: NispExamBatch) -> NispExamBatchResponse:
        plan = await db.get(Plan, batch.plan_id)
        occupied = await occupied_count(db, batch.plan_id)
        count_rows = (
            await db.execute(
                select(NispRegistration.status, func.count())
                .where(NispRegistration.batch_id == batch.id)
                .group_by(NispRegistration.status)
            )
        ).all()
        consultation = await db.scalar(
            select(CertConsultationGroup).where(
                CertConsultationGroup.plan_id == batch.plan_id
            )
        )
        return NispExamBatchResponse(
            id=batch.id,
            plan_id=batch.plan_id,
            level=batch.level,
            plan_name=plan.name if plan else "",
            plan_status=plan.status if plan else "",
            apply_start=plan.apply_start if plan else None,
            apply_end=plan.apply_end if plan else None,
            exam_date=plan.exam_date if plan else None,
            exam_location=plan.exam_location if plan else None,
            capacity=plan.capacity if plan else 0,
            occupied_count=occupied,
            training_org=batch.training_org,
            training_teacher=batch.training_teacher,
            training_address=batch.training_address,
            training_start=batch.training_start,
            training_end=batch.training_end,
            consultation_group_qrcode_url=(
                consultation.qrcode_url if consultation is not None else None
            ),
            level1_price_cents=batch.level1_price_cents,
            level2_price_cents=batch.level2_price_cents,
            payment_timeout_minutes=batch.payment_timeout_minutes,
            resubmission_window_hours=batch.resubmission_window_hours,
            max_resubmissions=batch.max_resubmissions,
            max_material_bytes=batch.max_material_bytes,
            registration_counts={status: int(count) for status, count in count_rows},
            created_at=batch.created_at,
            updated_at=batch.updated_at,
        )
