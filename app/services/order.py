import uuid

from sqlalchemy import func, select

from app.core.database import get_db_ctx
from app.core.exceptions import BusinessException, NotFoundException
from app.models.certification import Certification
from app.models.order import Order
from app.models.price_config import PriceConfig
from app.models.user_identity import UserIdentity
from app.schemas.common import PaginatedData
from app.schemas.order import OrderCreate, OrderDetailResponse, OrderFilter, OrderResponse


class OrderService:

    async def create_order(self, user_id: int, data: OrderCreate) -> OrderResponse:
        async with get_db_ctx() as db:
            cert = (
                await db.execute(
                    select(Certification).where(
                        Certification.code == data.cert_type,
                        Certification.is_active == True,
                    )
                )
            ).scalar_one_or_none()
            if cert is None:
                raise BusinessException("认证类型不存在或已下架")
            identity = await db.get(UserIdentity, user_id)
            user_type = "student" if (identity and identity.student_card_oss) else "enterprise"
            price_row = (
                await db.execute(
                    select(PriceConfig.price).where(
                        PriceConfig.cert_type == data.cert_type,
                        PriceConfig.user_type == user_type,
                        PriceConfig.is_active == True,
                    )
                )
            ).scalar_one_or_none()
            if price_row is None:
                raise BusinessException("该认证类型暂未配置价格")
            order = Order(
                user_id=user_id,
                cert_type=data.cert_type,
                candidate_name=data.candidate_name,
                candidate_phone=data.candidate_phone,
                candidate_idcard=data.candidate_idcard,
                price=price_row,
                out_trade_no=str(uuid.uuid4()),
            )
            db.add(order)
            await db.commit()
            await db.refresh(order)
            return OrderResponse.model_validate(order)

    async def list_orders(
        self, user_id: int, filters: OrderFilter | None, page: int, page_size: int
    ) -> PaginatedData[OrderResponse]:
        async with get_db_ctx() as db:
            base = select(Order).where(Order.user_id == user_id)
            if filters and filters.status:
                base = base.where(Order.status == filters.status)
            count_stmt = select(func.count()).select_from(base.subquery())
            total = (await db.execute(count_stmt)).scalar() or 0
            result = await db.execute(
                base.order_by(Order.id.desc()).offset((page - 1) * page_size).limit(page_size)
            )
            orders = result.scalars().all()
            return PaginatedData[OrderResponse](
                items=[OrderResponse.model_validate(o) for o in orders],
                total=total,
                page=page,
                page_size=page_size,
            )

    async def get_order(self, user_id: int, order_id: int) -> OrderDetailResponse:
        async with get_db_ctx() as db:
            order = await db.get(Order, order_id)
            if order is None or order.user_id != user_id:
                raise NotFoundException("订单")
            return OrderDetailResponse.model_validate(order)
