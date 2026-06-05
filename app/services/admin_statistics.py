from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.domain.order.src.index import Order
from app.domain.user.src.index import User


class AdminStatisticsService:

    async def dashboard(self) -> dict:
        now = datetime.now(timezone.utc)
        thirty_days_ago = now - timedelta(days=30)

        async with get_db_ctx() as db:
            total_users = (
                await db.execute(select(func.count()).select_from(User))
            ).scalar() or 0

            order_base = select(Order)
            total_orders = (
                await db.execute(
                    select(func.count()).select_from(order_base.subquery())
                )
            ).scalar() or 0

            recent_orders = (
                await db.execute(
                    select(func.count()).select_from(Order).where(
                        Order.created_at >= thirty_days_ago
                    )
                )
            ).scalar() or 0

            paid_orders = (
                await db.execute(
                    select(func.count()).select_from(Order).where(
                        Order.status.in_(["paid", "completed"])
                    )
                )
            ).scalar() or 0

            revenue = (
                await db.execute(
                    select(func.coalesce(func.sum(Order.price), 0)).select_from(Order).where(
                        Order.status.in_(["paid", "completed"])
                    )
                )
            ).scalar() or 0

            recent_revenue = (
                await db.execute(
                    select(func.coalesce(func.sum(Order.price), 0))
                    .select_from(Order)
                    .where(
                        Order.status.in_(["paid", "completed"]),
                        Order.created_at >= thirty_days_ago,
                    )
                )
            ).scalar() or 0

            conversion_rate = round(paid_orders / total_orders * 100, 2) if total_orders > 0 else 0

        return {
            "total_users": total_users,
            "total_orders": total_orders,
            "recent_orders_30d": recent_orders,
            "paid_orders": paid_orders,
            "revenue_fen": revenue,
            "recent_revenue_30d_fen": recent_revenue,
            "conversion_rate": conversion_rate,
        }
