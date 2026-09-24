"""Coupon application on orders, isolated from order creation."""

from datetime import datetime, timezone

from sqlalchemy import select

from app.adapter.database import get_db_ctx
from app.domain.order.src.index import Order
from app.domain.points_mall.src import PointsMallRedemption
from app.port.exceptions import BusinessException, NotFoundException
from app.schemas.order import OrderCouponAppliedResponse
from app.services.points_mall_services.shared import (
    calculate_discounted_price,
    check_coupon_scope,
)


class CouponCheckoutService:
    """Apply or remove a coupon on a pending order before payment."""

    async def apply(
        self,
        *,
        user_id: int,
        order_id: int,
        coupon_code: str,
    ) -> OrderCouponAppliedResponse:
        if not coupon_code:
            return await self._remove(user_id, order_id)
        return await self._apply(user_id, order_id, coupon_code)

    async def _remove(self, user_id: int, order_id: int) -> OrderCouponAppliedResponse:
        async with get_db_ctx() as db:
            async with db.begin():
                order = await self._get_order(db, user_id, order_id)
                if order.coupon_code:
                    redemption = await self._get_redemption(db, user_id, order.coupon_code)
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

    async def _apply(
        self, user_id: int, order_id: int, coupon_code: str
    ) -> OrderCouponAppliedResponse:
        async with get_db_ctx() as db:
            async with db.begin():
                order = await self._get_order(db, user_id, order_id)
                if order.coupon_code:
                    raise BusinessException("订单已使用优惠券，请先移除后再更换")

                redemption = await self._get_redemption(db, user_id, coupon_code)
                self._validate_coupon(redemption, order)

                final_price = calculate_discounted_price(
                    original_price_cents=order.price,
                    discount_type=redemption.discount_type,
                    discount_value=redemption.discount_value,
                )

                order.original_price = order.price
                order.price = final_price
                order.discount_amount = order.original_price - final_price
                order.coupon_code = coupon_code

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

    @staticmethod
    async def _get_order(db, user_id: int, order_id: int) -> Order:
        order = (
            await db.execute(
                select(Order)
                .where(Order.id == order_id, Order.user_id == user_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if order is None:
            raise NotFoundException("订单")
        if order.status != "pending":
            raise BusinessException("只能对待支付订单使用优惠券")
        return order

    @staticmethod
    async def _get_redemption(db, user_id: int, coupon_code: str) -> PointsMallRedemption:
        redemption = (
            await db.execute(
                select(PointsMallRedemption)
                .where(
                    PointsMallRedemption.coupon_code == coupon_code,
                    PointsMallRedemption.user_id == user_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if redemption is None:
            raise BusinessException("优惠券不存在")
        return redemption

    @staticmethod
    def _validate_coupon(redemption: PointsMallRedemption, order: Order) -> None:
        if redemption.status == "used":
            raise BusinessException("优惠券已被使用")
        if redemption.expires_at <= datetime.now(timezone.utc):
            raise BusinessException("优惠券已过期")
        if order.price < redemption.min_order_amount_cents:
            raise BusinessException("订单金额未达到优惠券最低要求")

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
