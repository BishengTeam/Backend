from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.port.exceptions import BusinessException, ConflictException, NotFoundException
from app.domain.order.src.index import (
    INVENTORY_LOCK_ACTION,
    ORDER_PAYMENT_EXPIRE_MINUTES,
    Order,
    PriceConfig,
    add_inventory_record,
    lock_certification_inventory,
    validate_extra_data,
)
from app.domain.user.src.index import UserRealname
from app.schemas.common import PaginatedData
from app.schemas.order import OrderCreate, OrderDetailResponse, OrderFilter, OrderResponse
from app.services.agreement_template import ensure_accepted
from app.utils.payment import generate_out_trade_no
from app.services.order_handlers import order_handler_registry

PRICE_TIER_NORMAL = "normal"
PRICE_TIER_STUDENT = "student"


def resolve_price_tier(user_type: str | None) -> str:
    return PRICE_TIER_STUDENT if user_type == PRICE_TIER_STUDENT else PRICE_TIER_NORMAL


class OrderService:

    async def create_order(self, user_id: int, data: OrderCreate) -> OrderResponse:
        if data.product_type == "RS-ZY":
            raise BusinessException("人社订单只能通过人社报名提交接口创建")
        async with get_db_ctx() as db:
            async with db.begin():
                # 认证报名需要实名验证
                if data.order_kind == "certification":
                    identity = (
                        await db.execute(
                            select(UserRealname).where(
                                UserRealname.user_id == user_id,
                                UserRealname.status == "verified",
                            )
                        )
                    ).scalar_one_or_none()
                    if identity is None:
                        raise BusinessException("请先完成实名认证")
                    await ensure_accepted(
                        db,
                        user_id,
                        "cert_registration",
                        message="请先阅读并同意认证报名信息处理授权协议",
                    )

                # Use the order handler registry (OCP)
                handler = order_handler_registry.get(data.order_kind)
                price = await handler.validate(db, user_id=user_id, data=data)
                inventory_id, inventory_change = await handler.lock_inventory(
                    db, product_type=data.product_type
                )

                expires_at = datetime.now(timezone.utc) + timedelta(
                    minutes=ORDER_PAYMENT_EXPIRE_MINUTES
                )
                order = Order(
                    user_id=user_id,
                    order_kind=data.order_kind,
                    product_type=data.product_type,
                    inventory_id=inventory_id,
                    candidate_name=data.candidate_name,
                    candidate_phone=data.candidate_phone,
                    candidate_idcard=data.candidate_idcard,
                    price=price,
                    status="pending",
                    out_trade_no=generate_out_trade_no("ORD"),
                    expires_at=expires_at,
                    extra_data=data.extra_data,
                    attachments=data.attachments,
                )
                db.add(order)
                await db.flush()
                if inventory_id is not None:
                    add_inventory_record(
                        db,
                        change=inventory_change,
                        order_id=order.id,
                        action=INVENTORY_LOCK_ACTION,
                        reason="order_created",
                    )
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
