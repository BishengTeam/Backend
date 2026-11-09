"""批次订单管理 v2：跨产品查询批次下的订单。"""
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.order.src.index import Order
from app.domain.user.src.index import User
from app.port.exceptions import ValidationException


class PlanOrderManagementService:
    async def list_orders(
        self,
        db: AsyncSession,
        *,
        plan_id: int,
        product_type: str | None = None,
        status: str | None = None,
        phone: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        conditions = [Order.plan_id == plan_id]
        if product_type:
            conditions.append(Order.product_type == product_type)
        if status:
            conditions.append(Order.status == status)
        if phone:
            conditions.append(Order.candidate_phone.contains(phone))

        total = (await db.execute(
            select(func.count()).select_from(Order).where(*conditions)
        )).scalar_one()

        orders = (await db.execute(
            select(Order)
            .where(*conditions)
            .order_by(Order.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )).scalars().all()

        from app.schemas.order import OrderResponse
        return {
            "items": [OrderResponse.model_validate(o, from_attributes=True) for o in orders],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
