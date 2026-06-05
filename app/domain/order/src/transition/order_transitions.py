"""订单状态转换：状态迁移、过期关单。"""
from datetime import datetime, timezone

from app.port.exceptions import ConflictException
from app.domain.order.src.model.order import Order
from app.domain.order.src.rule.order_rules import ORDER_STATUS_TRANSITIONS

def apply_order_status_transition(order: Order, target_status: str) -> bool:
    """对订单实例执行状态变更。返回是否实际变更。"""
    if order.status == target_status:
        return False
    allowed_targets = ORDER_STATUS_TRANSITIONS.get(order.status, set())
    if target_status not in allowed_targets:
        raise ConflictException(f"订单状态不允许从 {order.status} 变更为 {target_status}")
    order.status = target_status
    return True

DEFAULT_TIMEOUT_CLOSE_REASON = "payment_timeout"

def close_expired_pending_order(
    order: Order,
    *,
    now: datetime,
    close_reason: str = DEFAULT_TIMEOUT_CLOSE_REASON,
) -> bool:
    """判断 pending 订单是否已超时，超时则关单。"""
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
