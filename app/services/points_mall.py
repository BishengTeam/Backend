"""Points mall service: coupon templates, redemption, and checkout validation."""

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.domain.points_mall.src import PointsMallItem, PointsMallRedemption
from app.domain.user.src.index import UserPoints
from app.port.exceptions import BusinessException, ConflictException, NotFoundException
from app.schemas.points_mall import (
    MyCouponResponse,
    PointsMallItemCreate,
    PointsMallItemUpdate,
    PointsMallItemResponse,
    UsableCouponResponse,
    UserPointsMallItemResponse,
)

CATEGORY_LABELS = {
    "certification": "认证报名",
    "course": "课程",
    "quiz": "题库",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _generate_coupon_code() -> str:
    return f"PM-{secrets.token_hex(8).upper()}"


def _discount_label(discount_type: str, discount_value: int) -> str:
    if discount_type == "percent":
        return f"{discount_value / 10}折"
    return f"¥{discount_value / 100:.2f}"


def _scope_label(scope_type: str, scope_value: str | None) -> str:
    if scope_type == "global":
        return "全部商品"
    if scope_type == "category":
        return CATEGORY_LABELS.get(scope_value or "", scope_value or "指定分类")
    return f"指定商品: {scope_value}"


def _remaining_stock(item: PointsMallItem) -> int:
    if item.total_stock == 0:
        return -1  # unlimited
    return max(0, item.total_stock - item.total_redeemed)


def _effective_status(redemption: PointsMallRedemption) -> str:
    if redemption.status == "used":
        return "used"
    if redemption.expires_at <= _now():
        return "expired"
    return "unused"


def calculate_discounted_price(
    *,
    original_price_cents: int,
    discount_type: str,
    discount_value: int,
) -> int:
    if discount_type == "percent":
        return round(original_price_cents * discount_value / 100)
    return max(0, original_price_cents - discount_value)


def check_coupon_scope(
    *,
    scope_type: str,
    scope_value: str | None,
    product_type: str,
    product_category: str | None,
) -> bool:
    if scope_type == "global":
        return True
    if scope_type == "category":
        return product_category == scope_value
    if scope_type == "product":
        return product_type == scope_value
    return False


class PointsMallService:

    # ── Admin ──

    async def admin_create(self, data: PointsMallItemCreate) -> PointsMallItemResponse:
        if data.validity_type == "days" and not data.validity_days:
            raise BusinessException("固定天数模式必须指定天数")
        if data.validity_type == "fixed_date" and not data.valid_until:
            raise BusinessException("固定日期模式必须指定截止时间")
        if data.scope_type != "global" and not data.scope_value:
            raise BusinessException("指定范围必须填写范围值")
        if data.discount_type == "percent" and data.discount_value >= 100:
            raise BusinessException("百分比折扣不能大于或等于100")

        async with get_db_ctx() as db:
            item = PointsMallItem(**data.model_dump())
            db.add(item)
            await db.commit()
            await db.refresh(item)
            return self._item_response(item)

    async def admin_update(self, item_id: int, data: PointsMallItemUpdate) -> PointsMallItemResponse:
        async with get_db_ctx() as db:
            item = await db.get(PointsMallItem, item_id)
            if item is None:
                raise NotFoundException("积分商城商品")
            updates = data.model_dump(exclude_unset=True)
            if "validity_type" in updates and updates["validity_type"] == "days" and not updates.get("validity_days"):
                raise BusinessException("固定天数模式必须指定天数")
            if "validity_type" in updates and updates["validity_type"] == "fixed_date" and not updates.get("valid_until"):
                raise BusinessException("固定日期模式必须指定截止时间")
            for key, value in updates.items():
                setattr(item, key, value)
            await db.commit()
            await db.refresh(item)
            return self._item_response(item)

    async def admin_list(self, *, page: int = 1, page_size: int = 20) -> tuple[list[PointsMallItemResponse], int]:
        async with get_db_ctx() as db:
            total = await db.scalar(
                select(func.count()).select_from(PointsMallItem)
            ) or 0
            rows = (
                await db.execute(
                    select(PointsMallItem)
                    .order_by(PointsMallItem.sort_order.desc(), PointsMallItem.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            return [self._item_response(row) for row in rows], total

    async def admin_delete(self, item_id: int) -> None:
        async with get_db_ctx() as db:
            item = await db.get(PointsMallItem, item_id)
            if item is None:
                raise NotFoundException("积分商城商品")
            item.is_active = False
            await db.commit()

    # ── User: mall ──

    async def user_list_items(self, user_id: int) -> list[UserPointsMallItemResponse]:
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
                blocked = None
                if remaining == 0:
                    blocked = "已兑完"
                elif item.per_user_limit > 0 and user_redeemed >= item.per_user_limit:
                    blocked = "已达限兑次数"
                elif balance < item.points_cost:
                    blocked = "积分不足"
                if item.validity_type == "fixed_date" and item.valid_until and item.valid_until <= _now():
                    blocked = "已过期"

                results.append(UserPointsMallItemResponse(
                    id=item.id,
                    name=item.name,
                    description=item.description,
                    discount_type=item.discount_type,
                    discount_value=item.discount_value,
                    discount_label=_discount_label(item.discount_type, item.discount_value),
                    scope_label=_scope_label(item.scope_type, item.scope_value),
                    min_order_amount_cents=item.min_order_amount_cents,
                    points_cost=item.points_cost,
                    remaining_stock=remaining,
                    per_user_limit=item.per_user_limit,
                    user_redeemed_count=user_redeemed,
                    can_redeem=blocked is None,
                    redeem_blocked_reason=blocked,
                ))
            return results

    async def user_redeem(self, user_id: int, item_id: int) -> MyCouponResponse:
        async with get_db_ctx() as db:
            # Lock the item row
            item = (
                await db.execute(
                    select(PointsMallItem)
                    .where(PointsMallItem.id == item_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if item is None or not item.is_active:
                raise NotFoundException("积分商城商品")

            # Check stock
            if item.total_stock > 0 and item.total_redeemed >= item.total_stock:
                raise ConflictException("该优惠券已兑完")

            # Check per-user limit
            if item.per_user_limit > 0:
                user_redeemed = await db.scalar(
                    select(func.count()).select_from(PointsMallRedemption).where(
                        PointsMallRedemption.user_id == user_id,
                        PointsMallRedemption.item_id == item.id,
                    )
                ) or 0
                if user_redeemed >= item.per_user_limit:
                    raise ConflictException("已达限兑次数")

            # Check validity
            if item.validity_type == "fixed_date" and item.valid_until and item.valid_until <= _now():
                raise ConflictException("该优惠券已过期")

            # Deduct points
            points = (
                await db.execute(
                    select(UserPoints)
                    .where(UserPoints.user_id == user_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if points is None:
                points = UserPoints(user_id=user_id, balance=0)
                db.add(points)
            if points.balance < item.points_cost:
                raise BusinessException("积分不足")

            points.balance -= item.points_cost
            item.total_redeemed += 1

            # Calculate expiry
            if item.validity_type == "days" and item.validity_days:
                expires_at = _now() + timedelta(days=item.validity_days)
            elif item.valid_until:
                expires_at = item.valid_until
            else:
                expires_at = _now() + timedelta(days=365)  # fallback

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
            return self._coupon_response(redemption)

    # ── User: my coupons ──

    async def user_my_coupons(self, user_id: int, *, status: str | None = None) -> list[MyCouponResponse]:
        async with get_db_ctx() as db:
            stmt = select(PointsMallRedemption).where(
                PointsMallRedemption.user_id == user_id
            ).order_by(PointsMallRedemption.id.desc())
            rows = (await db.execute(stmt)).scalars().all()
            results = [self._coupon_response(r) for r in rows]
            if status:
                results = [r for r in results if r.status == status]
            return results

    async def user_usable_coupons(
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
                    .order_by(PointsMallRedemption.id.desc())
                )
            ).scalars().all()

            results = []
            for r in rows:
                if r.expires_at <= _now():
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
                    **self._coupon_response(r).model_dump(),
                    discounted_price_cents=discounted,
                    discount_amount_cents=order_amount_cents - discounted,
                ))
            return results

    # ── Internal: order integration ──

    async def apply_coupon_to_order(
        self,
        *,
        coupon_code: str,
        user_id: int,
        order_amount_cents: int,
        product_type: str,
        product_category: str | None,
        order_id: int,
    ) -> int:
        """Validate and consume a coupon. Returns discounted price. Raises on invalid."""
        async with get_db_ctx() as db:
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
            if redemption.status == "used":
                raise BusinessException("优惠券已被使用")
            if redemption.expires_at <= _now():
                raise BusinessException("优惠券已过期")
            if order_amount_cents < redemption.min_order_amount_cents:
                raise BusinessException("订单金额未达到优惠券最低要求")
            if not check_coupon_scope(
                scope_type=redemption.scope_type,
                scope_value=redemption.scope_value,
                product_type=product_type,
                product_category=product_category,
            ):
                raise BusinessException("优惠券不适用于该商品")

            discounted = calculate_discounted_price(
                original_price_cents=order_amount_cents,
                discount_type=redemption.discount_type,
                discount_value=redemption.discount_value,
            )
            redemption.status = "used"
            redemption.used_at = _now()
            redemption.order_id = order_id
            await db.commit()
            return discounted

    # ── Helpers ──

    def _item_response(self, item: PointsMallItem) -> PointsMallItemResponse:
        return PointsMallItemResponse(
            id=item.id,
            name=item.name,
            description=item.description,
            discount_type=item.discount_type,
            discount_value=item.discount_value,
            scope_type=item.scope_type,
            scope_value=item.scope_value,
            min_order_amount_cents=item.min_order_amount_cents,
            points_cost=item.points_cost,
            total_stock=item.total_stock,
            total_redeemed=item.total_redeemed,
            remaining_stock=_remaining_stock(item),
            per_user_limit=item.per_user_limit,
            validity_type=item.validity_type,
            validity_days=item.validity_days,
            valid_until=item.valid_until,
            is_active=item.is_active,
            sort_order=item.sort_order,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )

    def _coupon_response(self, r: PointsMallRedemption) -> MyCouponResponse:
        return MyCouponResponse(
            id=r.id,
            coupon_code=r.coupon_code,
            name=r.name_snapshot,
            discount_type=r.discount_type,
            discount_value=r.discount_value,
            discount_label=_discount_label(r.discount_type, r.discount_value),
            scope_type=r.scope_type,
            scope_value=r.scope_value,
            scope_label=_scope_label(r.scope_type, r.scope_value),
            min_order_amount_cents=r.min_order_amount_cents,
            status=_effective_status(r),
            expires_at=r.expires_at,
            used_at=r.used_at,
            order_id=r.order_id,
            created_at=r.created_at,
        )
