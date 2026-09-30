"""NISP admin service: batch management and registration review."""

from datetime import datetime, timezone

from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.domain.nisp.src import NispExamBatch, NispRegistration
from app.domain.plan.src.index import Plan
from app.port.exceptions import BusinessException, NotFoundException
from app.schemas.nisp import (
    NispExamBatchCreate,
    NispExamBatchResponse,
    NispExamBatchUpdate,
    NispRegistrationResponse,
    NispReviewDecisionRequest,
)
from app.services.nisp_registration import NispRegistrationService


def _now() -> datetime:
    return datetime.now(timezone.utc)


class NispAdminService:

    async def create_batch(self, data: NispExamBatchCreate) -> NispExamBatchResponse:
        async with get_db_ctx() as db:
            plan = await db.get(Plan, data.plan_id)
            if plan is None:
                raise NotFoundException("Plan")
            batch = NispExamBatch(**data.model_dump())
            db.add(batch)
            await db.commit()
            await db.refresh(batch)
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
            batch = await db.get(NispExamBatch, batch_id)
            if batch is None:
                raise NotFoundException("NISP 考试批次")
            for key, value in data.model_dump(exclude_unset=True).items():
                setattr(batch, key, value)
            await db.commit()
            await db.refresh(batch)
            return await self._response(db, batch)

    async def list_batches(
        self, *, page: int = 1, page_size: int = 20
    ) -> tuple[list[NispExamBatchResponse], int]:
        async with get_db_ctx() as db:
            total = await db.scalar(
                select(func.count()).select_from(NispExamBatch)
            ) or 0
            rows = (
                await db.execute(
                    select(NispExamBatch)
                    .order_by(NispExamBatch.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            return [await self._response(db, row) for row in rows], total

    async def publish_batch(self, batch_id: int) -> NispExamBatchResponse:
        async with get_db_ctx() as db:
            batch = await db.get(NispExamBatch, batch_id)
            if batch is None:
                raise NotFoundException("NISP 考试批次")
            plan = await db.get(Plan, batch.plan_id)
            if plan is None:
                raise NotFoundException("Plan")
            if plan.status == "draft":
                plan.status = "published"
                plan.published_at = _now()
            await db.commit()
            await db.refresh(batch)
            return await self._response(db, batch)

    async def close_registration(self, batch_id: int) -> NispExamBatchResponse:
        async with get_db_ctx() as db:
            batch = await db.get(NispExamBatch, batch_id)
            if batch is None:
                raise NotFoundException("NISP 考试批次")
            plan = await db.get(Plan, batch.plan_id)
            if plan is None:
                raise NotFoundException("Plan")
            plan.status = "registration_closed"
            plan.registration_closed_at = _now()
            await db.commit()
            await db.refresh(batch)
            return await self._response(db, batch)

    async def cancel_batch(self, batch_id: int) -> NispExamBatchResponse:
        async with get_db_ctx() as db:
            batch = await db.get(NispExamBatch, batch_id)
            if batch is None:
                raise NotFoundException("NISP 考试批次")
            plan = await db.get(Plan, batch.plan_id)
            if plan is None:
                raise NotFoundException("Plan")
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

    async def _response(self, db, batch: NispExamBatch) -> NispExamBatchResponse:
        plan = await db.get(Plan, batch.plan_id)
        occupied = (await db.scalar(
            select(func.count()).select_from(NispRegistration).where(
                NispRegistration.batch_id == batch.id,
                NispRegistration.status.in_(
                    ("pending_payment", "pending_review",
                     "rejected_awaiting_resubmission", "approved")
                ),
            )
        )) or 0
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
            level1_price_cents=batch.level1_price_cents,
            level2_price_cents=batch.level2_price_cents,
            payment_timeout_minutes=batch.payment_timeout_minutes,
            resubmission_window_hours=batch.resubmission_window_hours,
            max_resubmissions=batch.max_resubmissions,
            max_material_bytes=batch.max_material_bytes,
            created_at=batch.created_at,
            updated_at=batch.updated_at,
        )
