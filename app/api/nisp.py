from fastapi import APIRouter, Depends, Query

from sqlalchemy import func, select

from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.nisp import (
    NispBatchListItem,
    NispOrderCreate,
    NispRegistrationResponse,
    NispResubmitRequest,
)
from app.services.nisp_registration import NispRegistrationService

router = APIRouter(prefix="/nisp", tags=["NISP认证"])
_service = NispRegistrationService()


@router.get("/batches", response_model=APIResponse[list[NispBatchListItem]])
async def list_batches() -> APIResponse[list[NispBatchListItem]]:
    """List published NISP exam batches."""
    from app.adapter.database import get_db_ctx
    from app.domain.nisp.src import NispExamBatch
    from app.domain.plan.src.index import Plan
    from app.domain.order.src.index import Order

    async with get_db_ctx() as db:
        stmt = (
            select(NispExamBatch, Plan)
            .join(Plan, Plan.id == NispExamBatch.plan_id)
            .where(Plan.status == "published")
            .order_by(Plan.sort_order.desc(), NispExamBatch.id.desc())
        )
        rows = (await db.execute(stmt)).all()
        items = []
        for batch, plan in rows:
            occupied = (await db.scalar(
                select(func.count()).select_from(Order).where(
                    Order.plan_id == plan.id,
                    Order.status.in_(("pending", "paid", "completed")),
                )
            )) or 0
            remaining = max(0, plan.capacity - occupied) if plan.capacity > 0 else -1
            items.append(NispBatchListItem(
                id=batch.id,
                level=batch.level,
                name=plan.name,
                status=plan.status,
                apply_start=plan.apply_start,
                apply_end=plan.apply_end,
                exam_date=plan.exam_date,
                exam_location=plan.exam_location,
                remaining_count=remaining,
                level1_price_cents=batch.level1_price_cents,
                level2_price_cents=batch.level2_price_cents,
                payment_timeout_minutes=batch.payment_timeout_minutes,
                max_material_bytes=batch.max_material_bytes,
            ))
        return success(data=items)


@router.post("/orders", response_model=APIResponse[NispRegistrationResponse])
async def create_order(
    body: NispOrderCreate,
    current_user: User = Depends(get_current_user),
) -> APIResponse[NispRegistrationResponse]:
    return success(data=await _service.create_order(current_user.id, body))


@router.get("/registrations", response_model=APIResponse[PaginatedData[NispRegistrationResponse]])
async def list_registrations(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
) -> APIResponse[PaginatedData[NispRegistrationResponse]]:
    result = await _service.list_user_registrations(
        current_user.id, page=page, page_size=page_size
    )
    return success(data=PaginatedData(**result))


@router.get("/registrations/{registration_id}", response_model=APIResponse[NispRegistrationResponse])
async def get_registration(
    registration_id: int,
    current_user: User = Depends(get_current_user),
) -> APIResponse[NispRegistrationResponse]:
    return success(data=await _service.get_registration(current_user.id, registration_id))


@router.post(
    "/registrations/{registration_id}/cancel-payment",
    response_model=APIResponse[NispRegistrationResponse],
)
async def cancel_payment(
    registration_id: int,
    current_user: User = Depends(get_current_user),
) -> APIResponse[NispRegistrationResponse]:
    return success(
        data=await _service.cancel_pending_payment(current_user.id, registration_id)
    )


@router.post(
    "/registrations/{registration_id}/materials",
    response_model=APIResponse[NispRegistrationResponse],
)
async def resubmit_materials(
    registration_id: int,
    body: NispResubmitRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[NispRegistrationResponse]:
    return success(
        data=await _service.resubmit_materials(current_user.id, registration_id, body)
    )

