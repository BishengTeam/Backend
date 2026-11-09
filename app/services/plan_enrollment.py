"""批次报名容量锁 v2：按 plan_product.capacity 判断，支持同批次多产品。"""
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.order.src.index import Order
from app.domain.plan.src.index import Plan, PlanProduct
from app.models.cert_product import CertProduct
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

    async def lock_enrollable_plan_product(
        self,
        db: AsyncSession,
        *,
        plan_id: int,
        product_code: str,
        user_id: int,
        expected_vendor: str,
        now: datetime | None = None,
    ) -> tuple[Plan, PlanProduct, CertProduct]:
        """锁定批次×产品，校验窗口、容量、重复报名。返回三元组供下单使用。"""
        plan = (
            await db.execute(
                select(Plan).where(Plan.id == plan_id).with_for_update()
            )
        ).scalar_one_or_none()
        if plan is None:
            raise NotFoundException("批次")

        self.validate_application_window(plan, now=now)

        # 定位产品
        cert = (
            await db.execute(
                select(CertProduct).where(CertProduct.code == product_code).with_for_update()
            )
        ).scalar_one_or_none()
        if cert is None:
            raise BusinessException("认证产品不存在")
        if cert.type != expected_vendor.lower():
            raise BusinessException(f"该产品不属于{expected_vendor}认证")
        if not cert.is_active:
            raise BusinessException("该认证产品已下架")

        # 定位批次×产品关联
        plan_product = (
            await db.execute(
                select(PlanProduct)
                .where(
                    PlanProduct.plan_id == plan.id,
                    PlanProduct.product_id == cert.id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if plan_product is None:
            raise BusinessException("该批次不包含此产品")

        # 重复报名检查（同批次同产品）
        active_order_id = (
            await db.execute(
                select(Order.id)
                .where(
                    Order.plan_id == plan.id,
                    Order.product_type == product_code,
                    Order.user_id == user_id,
                    Order.status.in_(CAPACITY_OCCUPYING_ORDER_STATUSES),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if active_order_id is not None:
            raise ConflictException("您已报名该批次的产品，请勿重复提交")

        # 容量检查（按 plan_product.capacity）
        if plan_product.capacity > 0:
            occupied = (
                await db.execute(
                    select(func.count())
                    .select_from(Order)
                    .where(
                        Order.plan_id == plan.id,
                        Order.product_type == product_code,
                        Order.status.in_(CAPACITY_OCCUPYING_ORDER_STATUSES),
                    )
                )
            ).scalar_one()
            if occupied >= plan_product.capacity:
                raise BusinessException("该批次此产品名额已满")

        return plan, plan_product, cert

    async def lock_enrollable_plan(
        self,
        db: AsyncSession,
        *,
        plan_id: int,
        user_id: int,
        expected_vendor: str,
        now: datetime | None = None,
    ) -> Plan:
        """兼容旧签名：不指定 product_code 时锁定批次下第一个匹配 vendor 的产品。

        新代码应使用 lock_enrollable_plan_product。
        """
        # 先锁批次
        plan = (
            await db.execute(
                select(Plan).where(Plan.id == plan_id).with_for_update()
            )
        ).scalar_one_or_none()
        if plan is None:
            raise NotFoundException("批次")
        self.validate_application_window(plan, now=now)

        # 查找该 vendor 的产品
        plan_product = (
            await db.execute(
                select(PlanProduct, CertProduct)
                .join(CertProduct, PlanProduct.product_id == CertProduct.id)
                .where(
                    PlanProduct.plan_id == plan_id,
                    CertProduct.type == expected_vendor.lower(),
                    CertProduct.is_active.is_(True),
                )
                .with_for_update()
                .limit(1)
            )
        ).first()
        if plan_product is None:
            raise BusinessException(f"该批次不包含{expected_vendor}认证产品")

        _, cert = plan_product
        # 重复检查
        active_order_id = (
            await db.execute(
                select(Order.id)
                .where(
                    Order.plan_id == plan.id,
                    Order.product_type == cert.code,
                    Order.user_id == user_id,
                    Order.status.in_(CAPACITY_OCCUPYING_ORDER_STATUSES),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if active_order_id is not None:
            raise ConflictException("您已报名该批次，请勿重复提交")

        # 容量
        pp, _ = plan_product
        if pp.capacity > 0:
            occupied = (
                await db.execute(
                    select(func.count())
                    .select_from(Order)
                    .where(
                        Order.plan_id == plan.id,
                        Order.product_type == cert.code,
                        Order.status.in_(CAPACITY_OCCUPYING_ORDER_STATUSES),
                    )
                )
            ).scalar_one()
            if occupied >= pp.capacity:
                raise BusinessException("该批次名额已满")

        return plan
