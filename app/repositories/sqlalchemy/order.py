"""SQLAlchemy implementation of OrderRepository."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.order.src.index import Order


class SqlAlchemyOrderRepository:
    """Concrete repository using SQLAlchemy async session."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_id(self, order_id: int) -> Order | None:
        return await self._db.get(Order, order_id)

    async def get_by_id_for_update(self, order_id: int, user_id: int) -> Order | None:
        return (
            await self._db.execute(
                select(Order)
                .where(Order.id == order_id, Order.user_id == user_id)
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def list_by_user(
        self,
        user_id: int,
        *,
        status: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Order], int]:
        base = select(Order).where(Order.user_id == user_id)
        if status:
            base = base.where(Order.status == status)

        total = await self._db.scalar(
            select(func.count()).select_from(base.subquery())
        ) or 0

        rows = (
            await self._db.execute(
                base.order_by(Order.id.desc()).offset(offset).limit(limit)
            )
        ).scalars().all()
        return list(rows), total

    async def save(self, order: Order) -> Order:
        self._db.add(order)
        await self._db.flush()
        await self._db.refresh(order)
        return order
