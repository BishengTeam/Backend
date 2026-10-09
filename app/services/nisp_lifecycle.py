"""Shared NISP capacity accounting and refund transitions."""

from datetime import datetime, timezone

from sqlalchemy import func, or_, select

from app.domain.nisp.src import NispRegistration, NispRefundRequest
from app.domain.order.src.index import Order
from app.port.exceptions import ConflictException

ACTIVE_REGISTRATION_STATUSES = (
    "pending_payment", "pending_review", "rejected_awaiting_resubmission",
    "pending_refund_confirmation", "refund_processing", "approved",
)


def occupied_orders(plan_id: int):
    # Older NISP orders have no plan_id; count them through their registration.
    return select(Order).outerjoin(
        NispRegistration, NispRegistration.order_id == Order.id
    ).where(
        or_(Order.plan_id == plan_id, NispRegistration.plan_id == plan_id),
        Order.status.in_(("pending", "paid", "completed")),
    )


async def occupied_count(db, plan_id: int) -> int:
    return await db.scalar(select(func.count()).select_from(occupied_orders(plan_id).subquery())) or 0


async def request_refund(db, registration, order, *, reason_code: str,
                         reason_detail: str | None = None,
                         request_kind: str = "review_failed"):
    """Called with the registration locked, in the caller's transaction."""
    existing = await db.scalar(select(NispRefundRequest).where(
        NispRefundRequest.registration_id == registration.id,
        NispRefundRequest.status.in_(("requested", "approved", "processing", "failed")),
    ))
    if existing is not None:
        return existing
    if order is None or order.status not in {"paid", "completed"}:
        raise ConflictException("NISP 报名缺少可退款订单")
    registration.status = "pending_refund_confirmation"
    registration.resubmission_due_at = None
    registration.close_reason = reason_code
    refund = NispRefundRequest(
        registration_id=registration.id, order_id=order.id,
        user_id=registration.user_id, request_kind=request_kind,
        reason_code=reason_code, reason_detail=reason_detail,
        amount_cents=order.price, status="requested",
        requested_at=datetime.now(timezone.utc),
    )
    db.add(refund)
    return refund
