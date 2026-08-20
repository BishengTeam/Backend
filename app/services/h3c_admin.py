"""Administrator operations for H3C exam batches."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapter.database import get_db_ctx
from app.domain.certification.src.index import Certification
from app.domain.h3c.src.index import (
    H3C_REGISTRATION_TYPES,
    H3cExamBatch,
    H3cRegistration,
)
from app.domain.order.src.index import (
    Inventory,
    InventoryRecord,
    Order,
    apply_order_status_transition,
    release_inventory_lock,
)
from app.domain.plan.src.index import Plan
from app.port.exceptions import BusinessException, ConflictException, NotFoundException
from app.schemas.common import PaginatedData
from app.schemas.h3c_registration import (
    H3cExamBatchCreate,
    H3cExamBatchResponse,
    H3cExamBatchUpdate,
)
from app.services.admin_security_audit import AdminSecurityAuditService


H3C_PRICE_FIELDS = {
    "coupon": "coupon_price_cents",
    "student": "student_price_cents",
    "full": "full_price_cents",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class H3cAdminBatchService:
    async def create_batch(
        self,
        *,
        admin_id: int,
        data: H3cExamBatchCreate,
    ) -> H3cExamBatchResponse:
        async with get_db_ctx() as db:
            async with db.begin():
                certification = await db.scalar(
                    select(Certification)
                    .where(Certification.code == data.certification_code)
                    .with_for_update()
                )
                if certification is None or not certification.is_active:
                    raise BusinessException("H3C 认证类型不存在或已下架")
                if certification.vendor != "H3C":
                    raise BusinessException("仅支持创建 H3C 认证考试批次")
                duplicate = await db.scalar(
                    select(Plan.id).where(
                        Plan.product_type == certification.code,
                        Plan.name == data.name,
                    )
                )
                if duplicate is not None:
                    raise ConflictException("同名 H3C 考试批次已存在")
                plan = Plan(
                    product_type=certification.code,
                    name=data.name,
                    apply_start=data.apply_start,
                    apply_end=data.apply_end,
                    exam_date=data.exam_date,
                    capacity=data.capacity,
                    price_cents=data.full_price_cents,
                    exam_location=data.exam_location,
                    description=data.description,
                    sort_order=data.sort_order,
                    status="draft",
                )
                db.add(plan)
                await db.flush()
                batch = H3cExamBatch(
                    plan_id=plan.id,
                    exam_code=data.exam_code,
                    country=data.country,
                    language=data.language,
                    identity_tag=data.identity_tag,
                    training_org=data.training_org,
                    training_teacher=data.training_teacher,
                    training_address=data.training_address,
                    training_start=data.training_start,
                    training_end=data.training_end,
                    coupon_price_cents=data.coupon_price_cents,
                    student_price_cents=data.student_price_cents,
                    full_price_cents=data.full_price_cents,
                    payment_timeout_minutes=data.payment_timeout_minutes,
                    resubmission_window_hours=data.resubmission_window_hours,
                    max_resubmissions=data.max_resubmissions,
                    max_material_bytes=data.max_material_bytes,
                )
                db.add(batch)
                await db.flush()
                inventory = Inventory(
                    inventory_type="h3c_batch",
                    ref_code=f"h3c-batch-{batch.id}",
                    total_quota=data.capacity,
                    available_quota=data.capacity,
                    locked_quota=0,
                    sold_quota=0,
                    is_active=True,
                )
                db.add(inventory)
                await db.flush()
                db.add(
                    InventoryRecord(
                        inventory_id=inventory.id,
                        action="initialize",
                        quantity=data.capacity,
                        before_total_quota=0,
                        before_available_quota=0,
                        before_locked_quota=0,
                        before_sold_quota=0,
                        after_total_quota=data.capacity,
                        after_available_quota=data.capacity,
                        after_locked_quota=0,
                        after_sold_quota=0,
                        reason="h3c_batch_created",
                    )
                )
                AdminSecurityAuditService.append(
                    db,
                    action="h3c.batch.create",
                    result="succeeded",
                    actor_admin_id=admin_id,
                    summary={"batch_id": batch.id, "plan_id": plan.id},
                )
                return await self._response(db, batch)

    async def update_batch(
        self,
        *,
        admin_id: int,
        batch_id: int,
        data: H3cExamBatchUpdate,
    ) -> H3cExamBatchResponse:
        async with get_db_ctx() as db:
            async with db.begin():
                batch, plan = await self._lock(db, batch_id)
                if plan.status != "draft":
                    raise ConflictException("仅草稿批次允许编辑")
                payload = data.model_dump(exclude_unset=True)
                if "certification_code" in payload:
                    raise BusinessException("H3C 批次不支持修改认证类型")
                for field, value in payload.items():
                    if field in {
                        "coupon_price_cents", "student_price_cents", "full_price_cents"
                    }:
                        setattr(batch, field, value)
                        if field == "full_price_cents":
                            plan.price_cents = value
                    elif hasattr(plan, field):
                        setattr(plan, field, value)
                    elif hasattr(batch, field):
                        setattr(batch, field, value)
                if plan.apply_start and plan.apply_end and plan.apply_start >= plan.apply_end:
                    raise BusinessException("报名开始时间必须早于截止时间")
                if "capacity" in payload and payload["capacity"] != plan.capacity:
                    inventory = await db.scalar(
                        select(Inventory)
                        .where(
                            Inventory.inventory_type == "h3c_batch",
                            Inventory.ref_code == f"h3c-batch-{batch.id}",
                        )
                        .with_for_update()
                    )
                    if inventory is None:
                        raise ConflictException("H3C 批次库存不存在")
                    linked_orders = await db.scalar(
                        select(func.count())
                        .select_from(Order)
                        .where(
                            Order.plan_id == plan.id,
                            Order.status.in_(("pending", "paid", "completed")),
                        )
                    ) or 0
                    if linked_orders:
                        raise ConflictException("已有报名时不能修改批次总名额")
                    old_capacity = plan.capacity
                    new_capacity = int(payload["capacity"])
                    inventory.total_quota = new_capacity
                    inventory.available_quota = new_capacity
                    db.add(
                        InventoryRecord(
                            inventory_id=inventory.id,
                            action="adjust",
                            quantity=new_capacity - old_capacity,
                            before_total_quota=before.before_total_quota,
                            before_available_quota=before.before_available_quota,
                            before_locked_quota=0,
                            before_sold_quota=0,
                            after_total_quota=new_capacity,
                            after_available_quota=new_capacity,
                            after_locked_quota=0,
                            after_sold_quota=0,
                            reason="h3c_batch_draft_updated",
                        )
                    )
                await db.flush()
                AdminSecurityAuditService.append(
                    db,
                    action="h3c.batch.update",
                    result="succeeded",
                    actor_admin_id=admin_id,
                    summary={"batch_id": batch.id, "fields": sorted(payload)},
                )
                return await self._response(db, batch)

    async def publish_batch(self, *, admin_id: int, batch_id: int) -> H3cExamBatchResponse:
        return await self._transition(
            admin_id=admin_id,
            batch_id=batch_id,
            action="h3c.batch.publish",
            allowed={"draft"},
            target="published",
            require_window=True,
        )

    async def close_registration(self, *, admin_id: int, batch_id: int) -> H3cExamBatchResponse:
        return await self._transition(
            admin_id=admin_id,
            batch_id=batch_id,
            action="h3c.batch.close_registration",
            allowed={"published"},
            target="registration_closed",
        )

    async def finalize_batch(self, *, admin_id: int, batch_id: int) -> H3cExamBatchResponse:
        return await self._transition(
            admin_id=admin_id,
            batch_id=batch_id,
            action="h3c.batch.finalize",
            allowed={"registration_closed", "published"},
            target="finalized",
        )

    async def archive_batch(self, *, admin_id: int, batch_id: int) -> H3cExamBatchResponse:
        return await self._transition(
            admin_id=admin_id,
            batch_id=batch_id,
            action="h3c.batch.archive",
            allowed={"finalized", "cancelled"},
            target="archived",
        )

    async def cancel_batch(self, *, admin_id: int, batch_id: int) -> H3cExamBatchResponse:
        from app.services.h3c_registration import H3cRegistrationService

        async with get_db_ctx() as db:
            async with db.begin():
                batch, plan = await self._lock(db, batch_id)
                if plan.status not in {"published", "registration_closed"}:
                    raise ConflictException("当前批次状态不能取消")
                registrations = (
                    await db.execute(
                        select(H3cRegistration)
                        .where(H3cRegistration.batch_id == batch.id)
                        .with_for_update()
                    )
                ).scalars().all()
                for registration in registrations:
                    if registration.status in {"refunded_closed", "cancelled"}:
                        continue
                    order = await db.scalar(
                        select(Order)
                        .where(Order.id == registration.order_id)
                        .with_for_update()
                    )
                    if order is None:
                        continue
                    if order.status == "pending":
                        apply_order_status_transition(order, "closed")
                        order.closed_at = _now()
                        order.close_reason = "batch_cancelled"
                        await release_inventory_lock(db, order, reason="batch_cancelled")
                        registration.status = "cancelled"
                        registration.closed_at = order.closed_at
                        registration.close_reason = "batch_cancelled"
                    elif registration.status != "pending_refund_confirmation":
                        registration.status = "pending_refund_confirmation"
                        registration.resubmission_due_at = None
                        await H3cRegistrationService._create_refund_request(
                            db,
                            registration=registration,
                            request_kind="batch_cancelled",
                            reason_code="batch_cancelled",
                            reason_detail="H3C 考试批次已取消",
                            admin_id=admin_id,
                        )
                plan.status = "cancelled"
                plan.cancelled_at = _now()
                AdminSecurityAuditService.append(
                    db,
                    action="h3c.batch.cancel",
                    result="succeeded",
                    actor_admin_id=admin_id,
                    summary={"batch_id": batch.id, "registrations": len(registrations)},
                )
                return await self._response(db, batch)

    async def list_batches(
        self,
        *,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedData[H3cExamBatchResponse]:
        async with get_db_ctx() as db:
            stmt = select(H3cExamBatch).join(Plan, Plan.id == H3cExamBatch.plan_id)
            if status:
                stmt = stmt.where(Plan.status == status)
            total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
            rows = (
                await db.execute(
                    stmt.order_by(Plan.sort_order.desc(), H3cExamBatch.id.desc())
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

    async def get_batch(self, batch_id: int) -> H3cExamBatchResponse:
        async with get_db_ctx() as db:
            batch = await db.get(H3cExamBatch, batch_id)
            if batch is None:
                raise NotFoundException("H3C 考试批次")
            return await self._response(db, batch)

    async def _transition(
        self,
        *,
        admin_id: int,
        batch_id: int,
        action: str,
        allowed: set[str],
        target: str,
        require_window: bool = False,
    ) -> H3cExamBatchResponse:
        async with get_db_ctx() as db:
            async with db.begin():
                batch, plan = await self._lock(db, batch_id)
                if plan.status not in allowed:
                    raise ConflictException("当前批次状态不允许该操作")
                if require_window and (
                    not plan.apply_start
                    or not plan.apply_end
                    or not plan.exam_date
                ):
                    raise BusinessException("发布前必须配置报名时间、截止时间和考试时间")
                now = _now()
                plan.status = target
                if target == "published":
                    plan.published_at = now
                elif target == "registration_closed":
                    plan.registration_closed_at = now
                elif target == "finalized":
                    plan.finalized_at = now
                elif target == "cancelled":
                    plan.cancelled_at = now
                AdminSecurityAuditService.append(
                    db,
                    action=action,
                    result="succeeded",
                    actor_admin_id=admin_id,
                    summary={"batch_id": batch.id, "status": target},
                )
                return await self._response(db, batch)

    @staticmethod
    async def _lock(db: AsyncSession, batch_id: int) -> tuple[H3cExamBatch, Plan]:
        batch = await db.scalar(
            select(H3cExamBatch)
            .where(H3cExamBatch.id == batch_id)
            .with_for_update()
        )
        if batch is None:
            raise NotFoundException("H3C 考试批次")
        plan = await db.scalar(
            select(Plan).where(Plan.id == batch.plan_id).with_for_update()
        )
        if plan is None:
            raise NotFoundException("H3C 考试批次")
        return batch, plan

    @staticmethod
    async def _response(db: AsyncSession, batch: H3cExamBatch) -> H3cExamBatchResponse:
        plan = await db.get(Plan, batch.plan_id)
        certification = await db.scalar(
            select(Certification).where(Certification.code == plan.product_type)
        )
        occupied = await db.scalar(
            select(func.count())
            .select_from(Order)
            .where(
                Order.plan_id == plan.id,
                Order.status.in_(("pending", "paid", "completed")),
            )
        ) or 0
        return H3cExamBatchResponse(
            id=batch.id,
            plan_id=plan.id,
            certification_code=certification.code if certification else plan.product_type,
            name=plan.name,
            status=plan.status,
            apply_start=plan.apply_start,
            apply_end=plan.apply_end,
            exam_date=plan.exam_date,
            capacity=plan.capacity,
            occupied_count=int(occupied),
            remaining_count=max(0, plan.capacity - int(occupied)),
            exam_location=plan.exam_location,
            description=plan.description,
            sort_order=plan.sort_order,
            exam_code=batch.exam_code,
            identity_tag=batch.identity_tag,
            country=batch.country,
            language=batch.language,
            training_org=batch.training_org,
            training_teacher=batch.training_teacher,
            training_address=batch.training_address,
            training_start=batch.training_start,
            training_end=batch.training_end,
            payment_timeout_minutes=batch.payment_timeout_minutes,
            resubmission_window_hours=batch.resubmission_window_hours,
            max_resubmissions=batch.max_resubmissions,
            max_material_bytes=batch.max_material_bytes,
            prices=[
                {"registration_type": kind, "price_cents": getattr(batch, H3C_PRICE_FIELDS[kind])}
                for kind in H3C_REGISTRATION_TYPES
            ],
            published_at=plan.published_at,
            registration_closed_at=plan.registration_closed_at,
            cancelled_at=plan.cancelled_at,
            finalized_at=plan.finalized_at,
            created_at=batch.created_at,
            updated_at=batch.updated_at,
        )
