"""SQLAlchemy implementation of PointsMallRepository."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.points_mall.src import PointsMallItem, PointsMallRedemption


class SqlAlchemyPointsMallRepository:
    """Concrete repository using SQLAlchemy async session."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # Templates

    async def get_item(self, item_id: int) -> PointsMallItem | None:
        return await self._db.get(PointsMallItem, item_id)

    async def get_item_for_update(self, item_id: int) -> PointsMallItem | None:
        return (
            await self._db.execute(
                select(PointsMallItem)
                .where(PointsMallItem.id == item_id)
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def list_active_items(self) -> list[PointsMallItem]:
        return list(
            (
                await self._db.execute(
                    select(PointsMallItem)
                    .where(PointsMallItem.is_active.is_(True))
                    .order_by(
                        PointsMallItem.sort_order.desc(),
                        PointsMallItem.id.desc(),
                    )
                )
            ).scalars().all()
        )

    async def list_all_items(
        self, *, offset: int, limit: int
    ) -> tuple[list[PointsMallItem], int]:
        total = await self._db.scalar(
            select(func.count()).select_from(PointsMallItem)
        ) or 0
        rows = (
            await self._db.execute(
                select(PointsMallItem)
                .order_by(
                    PointsMallItem.sort_order.desc(),
                    PointsMallItem.id.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
        ).scalars().all()
        return list(rows), total

    async def save_item(self, item: PointsMallItem) -> PointsMallItem:
        self._db.add(item)
        await self._db.flush()
        await self._db.refresh(item)
        return item

    # Redemptions

    async def get_redemption_by_code(
        self, coupon_code: str, user_id: int
    ) -> PointsMallRedemption | None:
        return (
            await self._db.execute(
                select(PointsMallRedemption).where(
                    PointsMallRedemption.coupon_code == coupon_code,
                    PointsMallRedemption.user_id == user_id,
                )
            )
        ).scalar_one_or_none()

    async def get_redemption_for_update(
        self, coupon_code: str, user_id: int
    ) -> PointsMallRedemption | None:
        return (
            await self._db.execute(
                select(PointsMallRedemption)
                .where(
                    PointsMallRedemption.coupon_code == coupon_code,
                    PointsMallRedemption.user_id == user_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def list_user_redemptions(self, user_id: int) -> list[PointsMallRedemption]:
        return list(
            (
                await self._db.execute(
                    select(PointsMallRedemption)
                    .where(PointsMallRedemption.user_id == user_id)
                    .order_by(PointsMallRedemption.id.desc())
                )
            ).scalars().all()
        )

    async def count_user_redemptions(self, user_id: int, item_id: int) -> int:
        return (
            await self._db.scalar(
                select(func.count())
                .select_from(PointsMallRedemption)
                .where(
                    PointsMallRedemption.user_id == user_id,
                    PointsMallRedemption.item_id == item_id,
                )
            )
        ) or 0

    async def save_redemption(
        self, redemption: PointsMallRedemption
    ) -> PointsMallRedemption:
        self._db.add(redemption)
        await self._db.flush()
        await self._db.refresh(redemption)
        return redemption
