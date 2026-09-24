"""User-side coupon browsing, redemption, and listing."""

import secrets
from datetime import timedelta

from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.domain.points_mall.src import PointsMallItem, PointsMallRedemption
from app.domain.user.src.index import UserPoints
from app.port.exceptions import BusinessException, ConflictException
from app.schemas.points_mall import (
    MyCouponResponse,
    UsableCouponResponse,
    UserPointsMallItemResponse,
)
from app.services.points_mall_services.shared import (
    calculate_discounted_price,
    check_coupon_scope,
    discount_label,
    effective_status,
    now_utc,
    scope_label,
)


def _generate_coupon_code() -> str:
    return f"PM-{secrets.token_hex(8).upper()}"


def _remaining_stock(item: PointsMallItem) -> int:
    if item.total_stock == 0:
        return -1
    return max(0, item.total_stock - item.total_redeemed)


def _coupon_response(r: PointsMallRedemption) -> MyCouponResponse:
    return MyCouponResponse(
        id=r.id,
        coupon_code=r.coupon_code,
        name=r.name_snapshot,
        discount_type=r.discount_type,
        discount_value=r.discount_value,
        discount_label=discount_label(r.discount_type, r.discount_value),
        scope_type=r.scope_type,
        scope_value=r.scope_value,
        scope_label=scope_label(r.scope_type, r.scope_value),
        min_order_amount_cents=r.min_order_amount_cents,
        status=effective_status(r.status, r.expires_at),
        expires_at=r.expires_at,
        used_at=r.used_at,
        order_id=r.order_id,
        created_at=r.created_at,
    )


class UserPointsMallService:
    """User-facing coupon operations: browse, redeem, list, check usability."""

    async def list_items(self, user_id: int) -> list[UserPointsMallItemResponse]:
        async with get_db_ctx() as db:
            items = (
                await db.execute(
                    select(PointsMallItem)
                    .where(PointsMallItem.is_active.is_(True))
                    .order_by(PointsMallItem.sort_order.desc(), PointsMallItem.id.desc())
                )
            ).scalars().all()
            balance = await db.scalar(
                select(UserPoints.balance).where(UserPoints.user_id == user_id)
            ) or 0

            results = []
            for item in items:
                user_redeemed = await db.scalar(
                    select(func.count()).select_from(PointsMallRedemption).where(
                        PointsMallRedemption.user_id == user_id,
                        PointsMallRedemption.item_id == item.id,
                    )
                ) or 0
                remaining = _remaining_stock(item)
                blocked = self._blocked_reason(item, user_redeemed, balance, remaining)
                results.append(UserPointsMallItemResponse(
                    id=item.id,
                    name=item.name,
                    description=item.description,
                    discount_type=item.discount_type,
                    discount_value=item.discount_value,
                    discount_label=discount_label(item.discount_type, item.discount_value),
                    scope_label=scope_label(item.scope_type, item.scope_value),
                    min_order_amount_cents=item.min_order_amount_cents,
                    points_cost=item.points_cost,
                    remaining_stock=remaining,
                    per_user_limit=item.per_user_limit,
                    user_redeemed_count=user_redeemed,
                    can_redeem=blocked is None,
                    redeem_blocked_reason=blocked,
                ))
            return results

    async def redeem(self, user_id: int, item_id: int) -> MyCouponResponse:
        async with get_db_ctx() as db:
            item = (
                await db.execute(
                    select(PointsMallItem)
                    .where(PointsMallItem.id == item_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if item is None or not item.is_active:
                raise BusinessException("积分商城商品不存在")

            self._check_stock_and_limits(db, item, user_id)

            # Deduct points
            points = (
                await db.execute(
                    select(UserPoints).where(UserPoints.user_id == user_id).with_for_update()
                )
            ).scalar_one_or_none()
            if points is None:
                points = UserPoints(user_id=user_id, balance=0)
                db.add(points)
            if points.balance < item.points_cost:
                raise BusinessException("积分不足")

            points.balance -= item.points_cost
            item.total_redeemed += 1

            expires_at = self._calculate_expiry(item)
            redemption = PointsMallRedemption(
                user_id=user_id,
                item_id=item.id,
                points_spent=item.points_cost,
                coupon_code=_generate_coupon_code(),
                name_snapshot=item.name,
                discount_type=item.discount_type,
                discount_value=item.discount_value,
                scope_type=item.scope_type,
                scope_value=item.scope_value,
                min_order_amount_cents=item.min_order_amount_cents,
                expires_at=expires_at,
            )
            db.add(redemption)
            await db.commit()
            await db.refresh(redemption)
            return _coupon_response(redemption)

    async def my_coupons(
        self, user_id: int, *, status: str | None = None
    ) -> list[MyCouponResponse]:
        async with get_db_ctx() as db:
            rows = (
                await db.execute(
                    select(PointsMallRedemption)
                    .where(PointsMallRedemption.user_id == user_id)
                    .order_by(PointsMallRedemption.id.desc())
                )
            ).scalars().all()
            results = [_coupon_response(r) for r in rows]
            if status:
                results = [r for r in results if r.status == status]
            return results

    async def usable_coupons(
        self,
        user_id: int,
        *,
        order_amount_cents: int,
        product_type: str,
        product_category: str | None = None,
    ) -> list[UsableCouponResponse]:
        async with get_db_ctx() as db:
            rows = (
                await db.execute(
                    select(PointsMallRedemption)
                    .where(
                        PointsMallRedemption.user_id == user_id,
                        PointsMallRedemption.status == "unused",
                    )
                )
            ).scalars().all()

            results = []
            for r in rows:
                if r.expires_at <= now_utc():
                    continue
                if order_amount_cents < r.min_order_amount_cents:
                    continue
                if not check_coupon_scope(
                    scope_type=r.scope_type,
                    scope_value=r.scope_value,
                    product_type=product_type,
                    product_category=product_category,
                ):
                    continue
                discounted = calculate_discounted_price(
                    original_price_cents=order_amount_cents,
                    discount_type=r.discount_type,
                    discount_value=r.discount_value,
                )
                results.append(UsableCouponResponse(
                    **_coupon_response(r).model_dump(),
                    discounted_price_cents=discounted,
                    discount_amount_cents=order_amount_cents - discounted,
                ))
            return results

    @staticmethod
    def _blocked_reason(
        item: PointsMallItem, user_redeemed: int, balance: int, remaining: int
    ) -> str | None:
        if remaining == 0:
            return "已兑完"
        if item.per_user_limit > 0 and user_redeemed >= item.per_user_limit:
            return "已达限兑次数"
        if balance < item.points_cost:
            return "积分不足"
        if item.validity_type == "fixed_date" and item.valid_until and item.valid_until <= now_utc():
            return "已过期"
        return None

    @staticmethod
    def _calculate_expiry(item: PointsMallItem):
        if item.validity_type == "days" and item.validity_days:
            return now_utc() + timedelta(days=item.validity_days)
        if item.valid_until:
            return item.valid_until
        return now_utc() + timedelta(days=365)

    @staticmethod
    async def _check_stock_and_limits(db, item: PointsMallItem, user_id: int) -> None:
        if item.total_stock > 0 and item.total_redeemed >= item.total_stock:
            raise ConflictException("该优惠券已兑完")
        if item.per_user_limit > 0:
            user_redeemed = await db.scalar(
                select(func.count()).select_from(PointsMallRedemption).where(
                    PointsMallRedemption.user_id == user_id,
                    PointsMallRedemption.item_id == item.id,
                )
            ) or 0
            if user_redeemed >= item.per_user_limit:
                raise ConflictException("已达限兑次数")
        if item.validity_type == "fixed_date" and item.valid_until and item.valid_until <= now_utc():
            raise ConflictException("该优惠券已过期")
