from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.certification.src.index import Certification
from app.domain.order.src.index import Order
from app.domain.plan.src.index import Plan
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

        # v2 多产品：product_type=None 的 H3C 批次，经 h3c_batch_product 校验
        if plan.product_type is None and expected_vendor.lower() == "h3c":
            from app.domain.h3c.src.index import H3cExamBatch, H3cBatchProduct
            _bp_products = (
                await db.execute(
                    select(CertProduct)
                    .join(H3cBatchProduct, H3cBatchProduct.product_id == CertProduct.id)
                    .join(H3cExamBatch, H3cExamBatch.id == H3cBatchProduct.batch_id)
                    .where(H3cExamBatch.plan_id == plan.id)
                )
            ).scalars().all()
            if not _bp_products:
                raise BusinessException("批次关联的认证类型不存在")
            if any(p.type != "h3c" for p in _bp_products):
                raise BusinessException(f"该批次不属于{expected_vendor}认证")
            if any(not p.is_active for p in _bp_products):
                raise BusinessException("该认证类型已下架")
        else:
            # 认证产品体系：批次由认证管理（cert_product）创建，编码可能不存在于
            # 旧 certification 表；优先校验新表，历史批次再回退旧表
            product = (
                await db.execute(
                    select(CertProduct)
                    .where(CertProduct.code == plan.product_type)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if product is not None:
                if product.type != expected_vendor.lower():
                    raise BusinessException(f"该批次不属于{expected_vendor}认证")
                if not product.is_active:
                    raise BusinessException("该认证类型已下架")
            else:
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

        # U9: 多产品批次跳过 plan 级查重（两科共用同一 plan_id）
        # 由 h3c_registration 的科目级查重（batch_product_id 维度）兜底
        if plan.product_type is not None:
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
