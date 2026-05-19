from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.database import get_db_ctx
from app.core.exceptions import BusinessException
from app.models.order import Order
from app.services.inventory import release_inventory_lock
from app.services.order import apply_order_status_transition

DEFAULT_TIMEOUT_CLOSE_REASON = "payment_timeout"
MAX_CLOSE_REASON_LENGTH = 128


@dataclass(slots=True)
class CloseExpiredOrdersResult:
    scanned: int
    closed: int
    order_ids: list[int]


def close_expired_pending_order(
    order: Order,
    *,
    now: datetime,
    close_reason: str = DEFAULT_TIMEOUT_CLOSE_REASON,
) -> bool:
    if order.status != "pending":
        return False
    if order.expires_at is None:
        return False
    expires_at = order.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at > now:
        return False

    changed = apply_order_status_transition(order, "closed")
    if changed:
        order.closed_at = now
        order.close_reason = close_reason
    return changed


class OrderTimeoutCloseService:
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

        closed_at = now or datetime.now(timezone.utc)
        stmt = (
            select(Order)
            .where(
                Order.status == "pending",
                Order.expires_at.is_not(None),
                Order.expires_at <= closed_at,
            )
            .order_by(Order.id.asc())
            .with_for_update(skip_locked=True)
        )
        if limit is not None:
            stmt = stmt.limit(limit)

        async with get_db_ctx() as db:
            orders = (await db.execute(stmt)).scalars().all()
            closed_order_ids: list[int] = []

            for order in orders:
                if close_expired_pending_order(
                    order,
                    now=closed_at,
                    close_reason=close_reason,
                ):
                    await release_inventory_lock(db, order, reason=close_reason)
                    closed_order_ids.append(order.id)

            if closed_order_ids:
                await db.commit()

            return CloseExpiredOrdersResult(
                scanned=len(orders),
                closed=len(closed_order_ids),
                order_ids=closed_order_ids,
            )
