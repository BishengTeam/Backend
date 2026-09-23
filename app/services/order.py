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
from app.models.cert_product import CertProduct
from app.domain.user.src.index import UserRealname
from app.schemas.common import PaginatedData
from app.schemas.order import OrderCouponAppliedResponse, OrderCreate, OrderDetailResponse, OrderFilter, OrderResponse
from app.services.agreement_template import ensure_accepted
from app.utils.payment import generate_out_trade_no

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

                # 查询商品：优先新 cert_product，兼容旧 certification
                if data.order_kind == "certification":
                    cert = (
                        await db.execute(
                            select(CertProduct).where(
                                CertProduct.code == data.product_type,
                                CertProduct.is_active.is_(True),
                            )
                        )
                    ).scalar_one_or_none()
                    if cert is None:
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

    async def apply_coupon_to_order(
        self,
        *,
        user_id: int,
        order_id: int,
        coupon_code: str,
    ) -> OrderCouponAppliedResponse:
        """Apply or remove a points-mall coupon on a pending order before payment."""
        from datetime import datetime, timezone

        from app.domain.points_mall.src import PointsMallRedemption
        from app.services.points_mall import calculate_discounted_price, check_coupon_scope

        async with get_db_ctx() as db:
            async with db.begin():
                order = (
                    await db.execute(
                        select(Order).where(
                            Order.id == order_id, Order.user_id == user_id
                        ).with_for_update()
                    )
                ).scalar_one_or_none()
                if order is None:
                    raise NotFoundException("订单")
                if order.status != "pending":
                    raise BusinessException("只能对待支付订单使用优惠券")

                # Remove coupon: restore original price
                if not coupon_code:
                    if order.coupon_code:
                        # Release the coupon
                        redemption = (
                            await db.execute(
                                select(PointsMallRedemption).where(
                                    PointsMallRedemption.coupon_code == order.coupon_code,
                                    PointsMallRedemption.user_id == user_id,
                                ).with_for_update()
                            )
                        ).scalar_one_or_none()
                        if redemption:
                            redemption.status = "unused"
                            redemption.used_at = None
                            redemption.order_id = None
                        order.price = order.original_price or order.price
                        order.original_price = None
                        order.discount_amount = None
                        order.coupon_code = None
                    return OrderCouponAppliedResponse(
                        order_id=order.id,
                        original_price=order.original_price or order.price,
                        discount_amount=0,
                        final_price=order.price,
                        coupon_code=None,
                    )

                # Cannot stack: remove previous coupon first
                if order.coupon_code:
                    raise BusinessException("订单已使用优惠券，请先移除后再更换")

                # Validate and consume coupon
                redemption = (
                    await db.execute(
                        select(PointsMallRedemption).where(
                            PointsMallRedemption.coupon_code == coupon_code,
                            PointsMallRedemption.user_id == user_id,
                        ).with_for_update()
                    )
                ).scalar_one_or_none()
                if redemption is None:
                    raise BusinessException("优惠券不存在")
                if redemption.status == "used":
                    raise BusinessException("优惠券已被使用")
                if redemption.expires_at <= datetime.now(timezone.utc):
                    raise BusinessException("优惠券已过期")
                if order.price < redemption.min_order_amount_cents:
                    raise BusinessException("订单金额未达到优惠券最低要求")

                # Determine product category from order_kind
                product_category = None
                if order.order_kind == "certification":
                    product_category = "certification"
                elif order.order_kind == "course":
                    product_category = "course"

                if not check_coupon_scope(
                    scope_type=redemption.scope_type,
                    scope_value=redemption.scope_value,
                    product_type=order.product_type,
                    product_category=product_category,
                ):
                    raise BusinessException("优惠券不适用于该商品")

                # Calculate discounted price
                final_price = calculate_discounted_price(
                    original_price_cents=order.price,
                    discount_type=redemption.discount_type,
                    discount_value=redemption.discount_value,
                )

                # Apply to order
                order.original_price = order.price
                order.price = final_price
                order.discount_amount = order.price - final_price + 0  # order.price is now final
                order.discount_amount = order.original_price - final_price
                order.coupon_code = coupon_code

                # Consume coupon
                redemption.status = "used"
                redemption.used_at = datetime.now(timezone.utc)
                redemption.order_id = order.id

                return OrderCouponAppliedResponse(
                    order_id=order.id,
                    original_price=order.original_price,
                    discount_amount=order.discount_amount,
                    final_price=order.price,
                    coupon_code=coupon_code,
                )
