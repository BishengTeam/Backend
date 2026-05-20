from datetime import datetime

from sqlalchemy import func, select

from app.core.database import get_db_ctx
from app.core.exceptions import NotFoundException
from app.models.order import Order
from app.schemas.common import PaginatedData
from app.schemas.order import OrderDetailResponse, OrderFilter, OrderResponse
from app.services.order import ORDER_STATUS_TRANSITIONS, apply_order_status_transition


class AdminOrderService:

    async def list_orders(
        self,
        filters: OrderFilter | None,
        start_time: datetime | None,
        end_time: datetime | None,
        page: int,
        page_size: int,
    ) -> PaginatedData[OrderResponse]:
        async with get_db_ctx() as db:
            base = select(Order)
            if filters and filters.status:
                base = base.where(Order.status == filters.status)
            if start_time:
                base = base.where(Order.created_at >= start_time)
            if end_time:
                base = base.where(Order.created_at <= end_time)
            count_stmt = select(func.count()).select_from(base.subquery())
            total = (await db.execute(count_stmt)).scalar() or 0
            stmt = base.order_by(Order.id.desc()).offset((page - 1) * page_size).limit(page_size)
            result = await db.execute(stmt)
            orders = result.scalars().all()
            return PaginatedData[OrderResponse](
                items=[OrderResponse.model_validate(o) for o in orders],
                total=total,
                page=page,
                page_size=page_size,
            )

    async def get_order(self, order_id: int) -> OrderDetailResponse:
        async with get_db_ctx() as db:
            order = await db.get(Order, order_id)
            if order is None:
                raise NotFoundException("订单")
            return OrderDetailResponse.model_validate(order)

    async def refund_order(self, order_id: int) -> OrderDetailResponse:
        async with get_db_ctx() as db:
            order = await db.get(Order, order_id)
            if order is None:
                raise NotFoundException("订单")
            if order.status == "refunded":
                return OrderDetailResponse.model_validate(order)
            apply_order_status_transition(order, "refunded")
            # TODO: 接入微信退款接口 wechat_pay.refund(out_trade_no, price)
            await db.commit()
            await db.refresh(order)
            return OrderDetailResponse.model_validate(order)
