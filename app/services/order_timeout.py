from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import and_, or_, select

from app.adapter.database import get_db_ctx
from app.port.exceptions import BusinessException
from app.domain.order.src.index import (
    DEFAULT_TIMEOUT_CLOSE_REASON,
    Order,
    apply_order_status_transition,
    close_expired_pending_order,
    release_inventory_lock,
)
from app.services.order_fulfillment import OrderFulfillmentService

MAX_CLOSE_REASON_LENGTH = 128


@dataclass(slots=True)
class CloseExpiredOrdersResult:
    scanned: int
    closed: int
    order_ids: list[int]


class OrderTimeoutCloseService:
    def __init__(self) -> None:
        self.fulfillment = OrderFulfillmentService()

    async def close_expired_pending_orders(
        self,
        *,
        now: datetime | None = None,
        limit: int | None = None,
        close_reason: str = DEFAULT_TIMEOUT_CLOSE_REASON,
    ) -> CloseExpiredOrdersResult:
        if limit is not None and limit <= 0:
            raise BusinessException("limit must be greater than 0")
        if not close_reason or len(close_reason) > MAX_CLOSE_REASON_LENGTH:
            raise BusinessException("close_reason must be 1-128 characters")

        from app.models.offline_payment import OfflinePayment
        closed_at = now or datetime.now(timezone.utc)
        stmt = (
            select(Order)
            .outerjoin(OfflinePayment, OfflinePayment.order_id == Order.id)
            .where(
                Order.status == "pending",
                or_(
                    and_(Order.expires_at.is_not(None), Order.expires_at <= closed_at),
                    and_(Order.payment_channel == "bank_transfer", Order.offline_status == "returned", OfflinePayment.deadline <= closed_at),
                ),
            )
            .order_by(Order.id.asc())
            .with_for_update(of=Order, skip_locked=True)
        )
        if limit is not None:
            stmt = stmt.limit(limit)

        async with get_db_ctx() as db:
            orders = (await db.execute(stmt)).scalars().all()
            closed_order_ids: list[int] = []
            exceptions = 0

            for order in orders:
                if getattr(order, "payment_channel", None) == "bank_transfer" and order.offline_status == "returned":
                    order.offline_status = "exception"
                    order.expires_at = None
                    exceptions += 1
                    continue
                if close_expired_pending_order(
                    order,
                    now=closed_at,
                    close_reason=close_reason,
                ):
                    await release_inventory_lock(db, order, reason=close_reason)
                    from app.services.order import OrderService
                    await OrderService._release_coupon(db, order)
                    await self.fulfillment.on_closed(db, order)
                    closed_order_ids.append(order.id)

            if closed_order_ids or exceptions:
                await db.commit()

            return CloseExpiredOrdersResult(
                scanned=len(orders),
                closed=len(closed_order_ids),
                order_ids=closed_order_ids,
            )
