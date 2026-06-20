"""订单审核回调 — 薄封装层，调用 AdminOrderService"""

from app.schemas.admin import AdminOrderReview
from app.services.admin_order import AdminOrderService


async def approve(target_id: int, comment: str | None) -> None:
    """订单审核通过：paid → completed"""
    await AdminOrderService().review_order(
        order_id=target_id,
        data=AdminOrderReview(action="approve", comment=comment),
    )


async def reject(target_id: int, comment: str | None) -> None:
    """订单审核驳回并退款：paid → refunded"""
    await AdminOrderService().review_order(
        order_id=target_id,
        data=AdminOrderReview(action="reject_and_refund", comment=comment),
    )
