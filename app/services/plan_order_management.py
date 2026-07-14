from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapter.database import get_db_ctx
from app.domain.order.src.index import Order
from app.domain.plan.src.index import Plan
from app.domain.review.src.index import Review
from app.port.exceptions import NotFoundException
from app.schemas.common import PaginatedData
from app.schemas.order import OrderResponse
from app.schemas.review import ReviewResponse


class PlanOrderManagementService:
    async def _require_plan(
        self,
        db: AsyncSession,
        *,
        product_type: str,
        plan_id: int,
    ) -> Plan:
        plan = (
            await db.execute(
                select(Plan).where(
                    Plan.id == plan_id,
                    Plan.product_type == product_type,
                )
            )
        ).scalar_one_or_none()
        if plan is None:
            raise NotFoundException("批次")
        return plan

    async def list_orders(
        self,
        *,
        product_type: str,
        plan_id: int,
        status: str | None,
        phone: str | None,
        page: int,
        page_size: int,
    ) -> PaginatedData[OrderResponse]:
        async with get_db_ctx() as db:
            await self._require_plan(
                db,
                product_type=product_type,
                plan_id=plan_id,
            )

            base = select(Order).where(Order.plan_id == plan_id)
            if status:
                base = base.where(Order.status == status)
            if phone:
                base = base.where(Order.candidate_phone == phone)

            total = (
                await db.execute(select(func.count()).select_from(base.subquery()))
            ).scalar() or 0
            rows = (
                await db.execute(
                    base.order_by(Order.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            return PaginatedData[OrderResponse](
                items=[OrderResponse.model_validate(order) for order in rows],
                total=total,
                page=page,
                page_size=page_size,
            )

    async def list_approvals(
        self,
        *,
        product_type: str,
        plan_id: int,
        action: str | None,
        page: int,
        page_size: int,
    ) -> PaginatedData[ReviewResponse]:
        async with get_db_ctx() as db:
            await self._require_plan(
                db,
                product_type=product_type,
                plan_id=plan_id,
            )

            base = (
                select(Review)
                .join(Order, Review.target_id == Order.id)
                .where(
                    Review.target_type == "order",
                    Order.plan_id == plan_id,
                )
            )
            if action:
                base = base.where(Review.action == action)

            total = (
                await db.execute(select(func.count()).select_from(base.subquery()))
            ).scalar() or 0
            rows = (
                await db.execute(
                    base.order_by(Review.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            return PaginatedData[ReviewResponse](
                items=[ReviewResponse.model_validate(review) for review in rows],
                total=total,
                page=page,
                page_size=page_size,
            )
