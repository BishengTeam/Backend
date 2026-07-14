import time
import uuid
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
from app.domain.certification.src.index import Certification
from app.domain.user.src.index import UserRealname
from app.schemas.common import PaginatedData
from app.schemas.order import OrderCreate, OrderDetailResponse, OrderFilter, OrderResponse

PRICE_TIER_NORMAL = "normal"
PRICE_TIER_STUDENT = "student"


def resolve_price_tier(user_type: str | None) -> str:
    return PRICE_TIER_STUDENT if user_type == PRICE_TIER_STUDENT else PRICE_TIER_NORMAL


class OrderService:

    async def create_order(self, user_id: int, data: OrderCreate) -> OrderResponse:
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

                # 查询商品
                if data.order_kind == "certification":
                    cert = (
                        await db.execute(
                            select(Certification).where(
                                Certification.code == data.product_type,
                                Certification.is_active.is_(True),
                            )
                        )
                    ).scalar_one_or_none()
                    if cert is None:
                        raise BusinessException("认证类型不存在或已下架")

                    price_tier = resolve_price_tier(identity.user_type)
                    price_rows = (
                        await db.execute(
                            select(PriceConfig).where(
                                PriceConfig.product_type == data.product_type,
                                PriceConfig.user_type == price_tier,
                                PriceConfig.is_active.is_(True),
                            ).limit(2)
                        )
                    ).scalars().all()
                    if not price_rows:
                        raise BusinessException("该认证类型暂未配置价格")
                    if len(price_rows) > 1:
                        raise ConflictException("该认证类型价格配置重复，请联系管理员")
                    price = price_rows[0].price

                    validate_extra_data(data.product_type, data.extra_data)

                    inventory_change = await lock_certification_inventory(db, data.product_type)
                    inventory_id = inventory_change.inventory_id
                elif data.order_kind == "course":
                    raise BusinessException("课程订单请使用课程购买接口创建")
                else:
                    raise BusinessException(f"不支持的订单类型: {data.order_kind}")

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
                    out_trade_no=f"{user_id}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}",
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
