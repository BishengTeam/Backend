import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.core.database import get_db_ctx
from app.core.exceptions import BusinessException, ConflictException, NotFoundException
from app.models.certification import Certification
from app.models.order import Order
from app.models.price_config import PriceConfig
from app.models.user_identity import UserIdentity
from app.schemas.common import PaginatedData
from app.schemas.order import OrderCreate, OrderDetailResponse, OrderFilter, OrderResponse
from app.services.inventory import (
    INVENTORY_LOCK_ACTION,
    add_inventory_record,
    lock_certification_inventory,
)

ORDER_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"paid", "closed"},
    "paid": {"completed", "refunded"},
    "completed": {"refunded"},
    "refunded": set(),
    "closed": set(),
}

ORDER_PAYMENT_EXPIRE_MINUTES = 30


def apply_order_status_transition(order: Order, target_status: str) -> bool:
    if order.status == target_status:
        return False
    allowed_targets = ORDER_STATUS_TRANSITIONS.get(order.status, set())
    if target_status not in allowed_targets:
        raise ConflictException(f"订单状态不允许从 {order.status} 变更为 {target_status}")
    order.status = target_status
    return True


class OrderService:

    async def create_order(self, user_id: int, data: OrderCreate) -> OrderResponse:
        async with get_db_ctx() as db:
            async with db.begin():
                cert = (
                    await db.execute(
                        select(Certification).where(
                            Certification.code == data.cert_type,
                            Certification.is_active.is_(True),
                        )
                    )
                ).scalar_one_or_none()
                if cert is None:
                    raise BusinessException("认证类型不存在或已下架")
                identity = (
                    await db.execute(
                        select(UserIdentity).where(
                            UserIdentity.user_id == user_id,
                            UserIdentity.status == "verified",
                        )
                    )
                ).scalar_one_or_none()
                if identity is None:
                    raise BusinessException("请先完成实名认证")
                user_type = identity.user_type
                price_rows = (
                    await db.execute(
                        select(PriceConfig).where(
                            PriceConfig.cert_type == data.cert_type,
                            PriceConfig.user_type == user_type,
                            PriceConfig.is_active.is_(True),
                        ).limit(2)
                    )
                ).scalars().all()
                if not price_rows:
                    raise BusinessException("该认证类型暂未配置价格")
                if len(price_rows) > 1:
                    raise ConflictException("该认证类型价格配置重复，请联系管理员")

                inventory_change = await lock_certification_inventory(db, data.cert_type)
                expires_at = datetime.now(timezone.utc) + timedelta(
                    minutes=ORDER_PAYMENT_EXPIRE_MINUTES
                )
                order = Order(
                    user_id=user_id,
                    inventory_id=inventory_change.inventory_id,
                    cert_type=data.cert_type,
                    candidate_name=data.candidate_name,
                    candidate_phone=data.candidate_phone,
                    candidate_idcard=data.candidate_idcard,
                    price=price_rows[0].price,
                    status="pending",
                    out_trade_no=str(uuid.uuid4()),
                    expires_at=expires_at,
                )
                db.add(order)
                await db.flush()
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
