from datetime import datetime, timezone

from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.port.exceptions import BusinessException, NotFoundException
from app.models.coupon import Coupon, UserCoupon
from app.schemas.common import PaginatedData
from app.schemas.coupon import CouponAssignRequest, CouponResponse, CouponVerifyRequest


class CouponService:

    async def list_coupons(
        self,
        user_id: int,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
    ) -> PaginatedData[CouponResponse]:
        async with get_db_ctx() as db:
            base = (
                select(UserCoupon, Coupon)
                .join(Coupon, UserCoupon.coupon_id == Coupon.id)
                .where(UserCoupon.user_id == user_id)
            )
            if status:
                base = base.where(UserCoupon.status == status)

            count_stmt = select(func.count()).select_from(base.subquery())
            total = (await db.execute(count_stmt)).scalar() or 0

            stmt = (
                base.order_by(UserCoupon.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            result = await db.execute(stmt)
            rows = result.all()

            items = [
                CouponResponse(
                    id=coupon.id,
                    code=coupon.code,
                    type=coupon.type,
                    value=coupon.value,
                    min_order_amount=coupon.min_order_amount,
                    valid_from=coupon.valid_from,
                    valid_to=coupon.valid_to,
                    status=user_coupon.status,
                    used_at=user_coupon.used_at,
                )
                for user_coupon, coupon in rows
            ]

            return PaginatedData[CouponResponse](
                items=items,
                total=total,
                page=page,
                page_size=page_size,
            )

    async def assign_coupon(
        self,
        user_id: int,
        data: CouponAssignRequest,
    ) -> CouponResponse:
        async with get_db_ctx() as db:
            coupon = (
                await db.execute(
                    select(Coupon).where(Coupon.code == data.coupon_code)
                )
            ).scalar_one_or_none()

            if coupon is None:
                raise NotFoundException("优惠券")

            if not coupon.is_active:
                raise BusinessException("优惠券已失效")

            now = datetime.now(timezone.utc)
            if coupon.valid_from and now < coupon.valid_from:
                raise BusinessException("优惠券尚未生效")
            if coupon.valid_to and now > coupon.valid_to:
                raise BusinessException("优惠券已过期")

            existing = (
                await db.execute(
                    select(UserCoupon).where(
                        UserCoupon.user_id == user_id,
                        UserCoupon.coupon_id == coupon.id,
                    )
                )
            ).scalar_one_or_none()

            if existing is not None:
                raise BusinessException("您已领取过该优惠券")

            user_coupon = UserCoupon(
                user_id=user_id,
                coupon_id=coupon.id,
                status="unused",
            )
            db.add(user_coupon)
            await db.commit()
            await db.refresh(user_coupon)

            return CouponResponse(
                id=coupon.id,
                code=coupon.code,
                type=coupon.type,
                value=coupon.value,
                min_order_amount=coupon.min_order_amount,
                valid_from=coupon.valid_from,
                valid_to=coupon.valid_to,
                status=user_coupon.status,
                used_at=user_coupon.used_at,
            )

    async def verify_coupon(
        self,
        user_id: int,
        data: CouponVerifyRequest,
    ) -> CouponResponse:
        async with get_db_ctx() as db:
            coupon = (
                await db.execute(
                    select(Coupon).where(Coupon.code == data.coupon_code)
                )
            ).scalar_one_or_none()

            if coupon is None:
                raise NotFoundException("优惠券")

            user_coupon = (
                await db.execute(
                    select(UserCoupon).where(
                        UserCoupon.user_id == user_id,
                        UserCoupon.coupon_id == coupon.id,
                    )
                )
            ).scalar_one_or_none()

            if user_coupon is None:
                raise NotFoundException("用户优惠券")

            if user_coupon.status != "unused":
                raise BusinessException("优惠券已使用或已过期")

            now = datetime.now(timezone.utc)
            if coupon.valid_to and now > coupon.valid_to:
                raise BusinessException("优惠券已过期")

            user_coupon.status = "used"
            user_coupon.used_at = now
            await db.commit()
            await db.refresh(user_coupon)

            return CouponResponse(
                id=coupon.id,
                code=coupon.code,
                type=coupon.type,
                value=coupon.value,
                min_order_amount=coupon.min_order_amount,
                valid_from=coupon.valid_from,
                valid_to=coupon.valid_to,
                status=user_coupon.status,
                used_at=user_coupon.used_at,
            )
