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
            if filters:
                if filters.status:
                    base = base.where(Order.status == filters.status)
                if filters.cert_type:
                    base = base.where(Order.cert_type == filters.cert_type)
                if filters.phone:
                    base = base.where(Order.candidate_phone == filters.phone)
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

    async def reconciliation(self, date: str) -> dict:
        from datetime import datetime as dt

        date_start = dt.fromisoformat(date)
        date_end = dt.fromisoformat(f"{date}T23:59:59")
        async with get_db_ctx() as db:
            stmt = select(Order).where(
                Order.created_at >= date_start,
                Order.created_at <= date_end,
            )
            result = await db.execute(stmt)
            orders = result.scalars().all()
            order_total = sum(o.price for o in orders if o.status != "refunded")
            refund_total = sum(o.price for o in orders if o.status == "refunded")
            net_income = order_total - refund_total
            order_count = len(orders)
            return {
                "order_total": order_total,
                "refund_total": refund_total,
                "net_income": net_income,
                "order_count": order_count,
            }

    async def export_orders(
        self,
        filters: OrderFilter | None,
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> str:
        import csv
        import io

        async with get_db_ctx() as db:
            base = select(Order)
            if filters:
                if filters.status:
                    base = base.where(Order.status == filters.status)
                if filters.cert_type:
                    base = base.where(Order.cert_type == filters.cert_type)
                if filters.phone:
                    base = base.where(Order.candidate_phone == filters.phone)
            if start_time:
                base = base.where(Order.created_at >= start_time)
            if end_time:
                base = base.where(Order.created_at <= end_time)
            stmt = base.order_by(Order.id.desc())
            result = await db.execute(stmt)
            orders = result.scalars().all()
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["ID", "UserID", "CertType", "CandidateName", "CandidatePhone", "Price", "Status", "CreatedAt"])
            for o in orders:
                writer.writerow([o.id, o.user_id, o.cert_type, o.candidate_name, o.candidate_phone, o.price, o.status, o.created_at])
            return output.getvalue()
