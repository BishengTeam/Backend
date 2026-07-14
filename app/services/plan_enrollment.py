from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.certification.src.index import Certification
from app.domain.order.src.index import Order
from app.domain.plan.src.index import Plan
from app.port.exceptions import BusinessException, ConflictException, NotFoundException


CAPACITY_OCCUPYING_ORDER_STATUSES = ("pending", "paid", "completed")


class PlanEnrollmentService:
    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @classmethod
    def validate_application_window(
        cls,
        plan: Plan,
        *,
        now: datetime | None = None,
    ) -> None:
        if plan.status != "published":
            raise BusinessException("该批次当前不可报名")
        if plan.apply_start is None or plan.apply_end is None:
            raise BusinessException("该批次未配置完整的报名时间")

        current = cls._as_utc(now or datetime.now(timezone.utc))
        apply_start = cls._as_utc(plan.apply_start)
        apply_end = cls._as_utc(plan.apply_end)
        if current < apply_start:
            raise BusinessException("该批次报名尚未开始")
        if current > apply_end:
            raise BusinessException("该批次报名已截止")

    async def lock_enrollable_plan(
        self,
        db: AsyncSession,
        *,
        plan_id: int,
        user_id: int,
        expected_vendor: str,
        now: datetime | None = None,
    ) -> Plan:
        plan = (
            await db.execute(
                select(Plan)
                .where(Plan.id == plan_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if plan is None:
            raise NotFoundException("批次")

        self.validate_application_window(plan, now=now)

        certification = (
            await db.execute(
                select(Certification)
                .where(Certification.code == plan.product_type)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if certification is None:
            raise BusinessException("批次关联的认证类型不存在")
        if certification.vendor != expected_vendor:
            raise BusinessException(f"该批次不属于{expected_vendor}认证")
        if not certification.is_active:
            raise BusinessException("该认证类型已下架")

        active_order_id = (
            await db.execute(
                select(Order.id)
                .where(
                    Order.plan_id == plan.id,
                    Order.user_id == user_id,
                    Order.status.in_(CAPACITY_OCCUPYING_ORDER_STATUSES),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if active_order_id is not None:
            raise ConflictException("您已报名该批次，请勿重复提交")

        if plan.capacity > 0:
            occupied = (
                await db.execute(
                    select(func.count())
                    .select_from(Order)
                    .where(
                        Order.plan_id == plan.id,
                        Order.status.in_(CAPACITY_OCCUPYING_ORDER_STATUSES),
                    )
                )
            ).scalar_one()
            if occupied >= plan.capacity:
                raise BusinessException("该批次名额已满")

        return plan
