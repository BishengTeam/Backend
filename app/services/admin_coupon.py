import secrets

from sqlalchemy import func, select

from app.core.database import get_db_ctx
from app.core.exceptions import NotFoundException
from app.models.coupon import Coupon
from app.schemas.admin_coupon import AdminCouponBatchCreate, AdminCouponCreate, AdminCouponListItem
from app.schemas.common import PaginatedData


class AdminCouponService:

    async def list_coupons(
        self, page: int, page_size: int
    ) -> PaginatedData[AdminCouponListItem]:
        async with get_db_ctx() as db:
            base = select(Coupon)
            count_stmt = select(func.count()).select_from(base.subquery())
            total = (await db.execute(count_stmt)).scalar() or 0
            stmt = base.order_by(Coupon.id.desc()).offset((page - 1) * page_size).limit(page_size)
            result = await db.execute(stmt)
            coupons = result.scalars().all()
            return PaginatedData[AdminCouponListItem](
                items=[AdminCouponListItem.model_validate(c) for c in coupons],
                total=total,
                page=page,
                page_size=page_size,
            )

    async def create(self, data: AdminCouponCreate) -> AdminCouponListItem:
        async with get_db_ctx() as db:
            coupon = Coupon(**data.model_dump())
            db.add(coupon)
            await db.commit()
            await db.refresh(coupon)
            return AdminCouponListItem.model_validate(coupon)

    async def batch_create(self, data: AdminCouponBatchCreate) -> list[AdminCouponListItem]:
        async with get_db_ctx() as db:
            coupons = []
            for _ in range(data.count):
                suffix = secrets.token_hex(4)
                code = f"{data.code_prefix}{suffix}"
                coupon = Coupon(
                    code=code,
                    type=data.type,
                    value=data.value,
                    min_order_amount=data.min_order_amount,
                    valid_from=data.valid_from,
                    valid_to=data.valid_to,
                )
                db.add(coupon)
                coupons.append(coupon)
            await db.commit()
            for c in coupons:
                await db.refresh(c)
            return [AdminCouponListItem.model_validate(c) for c in coupons]

    async def deactivate(self, coupon_id: int) -> None:
        async with get_db_ctx() as db:
            coupon = await db.get(Coupon, coupon_id)
            if coupon is None:
                raise NotFoundException("优惠券")
            coupon.is_active = False
            await db.commit()
