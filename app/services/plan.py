"""认证批次服务 v2：跨厂商批次 CRUD + 生命周期 + 产品/时段管理。"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.adapter.database import get_db_ctx
from app.domain.order.src.index import Order
from app.domain.plan.src.index import Plan, PlanProduct, PlanTimeSegment
from app.models.cert_product import CertProduct
from app.models.renshe_project import RensheProjectConfig
from app.port.config import settings
from app.port.exceptions import BusinessException, ConflictException, NotFoundException, ValidationException
from app.schemas.plan import (
    BatchCreate,
    BatchListResponse,
    BatchResponse,
    BatchUpdate,
    PlanImpactAction,
    PlanImpactResponse,
    PlanProductCreate,
    PlanProductUpdate,
    PlanProductResponse,
    TimeSegmentCreate,
    TimeSegmentResponse,
    TimeSegmentUpdate,
)

CAPACITY_OCCUPYING_ORDER_STATUSES = ("pending", "paid", "completed")


class PlanService:
    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_datetime(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=ZoneInfo(settings.APP_TIMEZONE))
        return value.astimezone(timezone.utc)

    @classmethod
    def _validate_schedule(
        cls,
        *,
        apply_start: datetime | None,
        apply_end: datetime | None,
        exam_date: datetime | None,
        require_window: bool = False,
        now: datetime | None = None,
    ) -> None:
        start = cls._normalize_datetime(apply_start)
        end = cls._normalize_datetime(apply_end)
        exam = cls._normalize_datetime(exam_date)
        if require_window and (start is None or end is None):
            raise ValidationException("发布批次前必须设置报名开始时间和截止时间")
        if start is not None and end is not None and start >= end:
            raise ValidationException("报名开始时间必须早于报名截止时间")
        if exam is not None and end is not None and exam < end:
            raise ValidationException("考试日期不能早于报名截止时间")
        if require_window and end is not None:
            current = cls._normalize_datetime(now or datetime.now(timezone.utc))
            if end <= current:
                raise ValidationException("报名截止时间必须晚于当前时间")

    @staticmethod
    async def _get_plan(db: AsyncSession, plan_id: int, *, for_update: bool = False) -> Plan:
        stmt = select(Plan).where(Plan.id == plan_id)
        if for_update:
            stmt = stmt.with_for_update()
        plan = (await db.execute(stmt)).scalar_one_or_none()
        if plan is None:
            raise NotFoundException("批次")
        return plan

    @staticmethod
    async def _get_products(db: AsyncSession, plan_id: int) -> list[tuple[PlanProduct, CertProduct]]:
        result = await db.execute(
            select(PlanProduct, CertProduct)
            .join(CertProduct, PlanProduct.product_id == CertProduct.id)
            .where(PlanProduct.plan_id == plan_id)
            .order_by(PlanProduct.id)
        )
        return list(result.all())

    @staticmethod
    async def _get_segments(db: AsyncSession, plan_id: int) -> list[PlanTimeSegment]:
        result = await db.execute(
            select(PlanTimeSegment)
            .where(PlanTimeSegment.plan_id == plan_id)
            .order_by(PlanTimeSegment.sort_order, PlanTimeSegment.id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def _enrolled_counts(db: AsyncSession, plan_id: int) -> dict[int, int]:
        """按 plan_product_id 统计占用名额的订单数。"""
        result = await db.execute(
            select(Order.plan_id, Order.product_type, func.count())
            .where(
                Order.plan_id == plan_id,
                Order.status.in_(CAPACITY_OCCUPYING_ORDER_STATUSES),
            )
            .group_by(Order.plan_id, Order.product_type)
        )
        # plan_id 是批次 ID，product_type 是产品 code；需要映射到 plan_product
        counts_by_code: dict[str, int] = {}
        for _, product_code, count in result.all():
            counts_by_code[product_code] = count
        return counts_by_code  # caller 通过 product.code 查

    # ------------------------------------------------------------------
    # 批次 CRUD
    # ------------------------------------------------------------------

    @classmethod
    async def list_batches(
        cls,
        db: AsyncSession,
        *,
        status: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[BatchListResponse], int]:
        conditions = []
        if status:
            conditions.append(Plan.status == status)
        base = select(Plan).where(*conditions) if conditions else select(Plan)
        total = (await db.execute(
            select(func.count()).select_from(base.subquery())
        )).scalar_one()
        plans = (await db.execute(
            base.order_by(Plan.sort_order, Plan.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )).scalars().all()

        items: list[BatchListResponse] = []
        for plan in plans:
            products = await cls._get_products(db, plan.id)
            enrolled = sum(
                p.capacity for p, _ in products if p.capacity > 0
            )  # 粗略：仅返回产品数和总容量
            items.append(BatchListResponse(
                id=plan.id,
                name=plan.name,
                status=plan.status,
                apply_start=plan.apply_start,
                apply_end=plan.apply_end,
                exam_date=plan.exam_date,
                product_count=len(products),
                total_enrolled=0,  # 列表页不逐个统计，详情页有精确数
            ))
        return items, total

    @classmethod
    async def get_batch(cls, db: AsyncSession, plan_id: int) -> BatchResponse:
        plan = await cls._get_plan(db, plan_id)
        products_data = await cls._get_products(db, plan_id)
        segments = await cls._get_segments(db, plan_id)
        enrolled_by_code = await cls._enrolled_counts(db, plan_id)

        product_responses: list[PlanProductResponse] = []
        total_enrolled = 0
        for pp, cert in products_data:
            enrolled = enrolled_by_code.get(cert.code, 0)
            total_enrolled += enrolled
            remaining = None if pp.capacity == 0 else max(0, pp.capacity - enrolled)
            product_responses.append(PlanProductResponse(
                id=pp.id,
                plan_id=pp.plan_id,
                product_id=pp.product_id,
                product_code=cert.code,
                product_name=cert.name,
                product_type=cert.type,
                capacity=pp.capacity,
                enrolled=enrolled,
                remaining=remaining,
                renshe_config_id=pp.renshe_config_id,
                occupation_name=pp.occupation_name,
                occupation_code=pp.occupation_code,
                skill_level=pp.skill_level,
                exam_type=pp.exam_type,
                application_type=pp.application_type,
                renshe_config=pp.renshe_config,
            ))

        return BatchResponse(
            id=plan.id,
            name=plan.name,
            apply_start=plan.apply_start,
            apply_end=plan.apply_end,
            exam_date=plan.exam_date,
            exam_location=plan.exam_location,
            description=plan.description,
            contact_name=plan.contact_name,
            contact_phone=plan.contact_phone,
            sort_order=plan.sort_order,
            status=plan.status,
            published_at=plan.published_at,
            registration_closed_at=plan.registration_closed_at,
            cancelled_at=plan.cancelled_at,
            finalized_at=plan.finalized_at,
            created_at=plan.created_at,
            updated_at=plan.updated_at,
            products=product_responses,
            time_segments=[
                TimeSegmentResponse.model_validate(s) for s in segments
            ],
            total_enrolled=total_enrolled,
        )

    @classmethod
    async def get_user_batch(cls, db: AsyncSession, plan_id: int) -> "UserBatchResponse":
        """用户端批次详情：字段白名单投影，不暴露内部配置。"""
        from app.schemas.plan import UserBatchResponse, UserPlanProductResponse
        full = await cls.get_batch(db, plan_id)
        return UserBatchResponse(
            id=full.id,
            name=full.name,
            apply_start=full.apply_start,
            apply_end=full.apply_end,
            exam_date=full.exam_date,
            exam_location=full.exam_location,
            description=full.description,
            status=full.status,
            products=[
                UserPlanProductResponse(
                    id=p.id,
                    product_code=p.product_code,
                    product_name=p.product_name,
                    product_type=p.product_type,
                    capacity=p.capacity,
                    enrolled=p.enrolled,
                    remaining=p.remaining,
                    skill_level=p.skill_level,
                    application_type=p.application_type,
                )
                for p in full.products
            ],
            time_segments=full.time_segments,
            total_enrolled=full.total_enrolled,
        )

    @classmethod
    async def create_batch(cls, db: AsyncSession, data: BatchCreate) -> BatchResponse:
        cls._validate_schedule(
            apply_start=data.apply_start,
            apply_end=data.apply_end,
            exam_date=data.exam_date,
        )
        # name 全局唯一
        existing = await db.execute(select(Plan.id).where(Plan.name == data.name).limit(1))
        if existing.scalar_one_or_none() is not None:
            raise ConflictException(f"批次名称已存在：{data.name}")

        plan = Plan(
            name=data.name,
            apply_start=cls._normalize_datetime(data.apply_start),
            apply_end=cls._normalize_datetime(data.apply_end),
            exam_date=cls._normalize_datetime(data.exam_date),
            exam_location=data.exam_location,
            description=data.description,
            contact_name=data.contact_name,
            contact_phone=data.contact_phone,
            sort_order=data.sort_order,
        )
        db.add(plan)
        await db.flush()

        # 添加产品
        for product_data in data.products:
            await cls._add_product_row(db, plan.id, product_data)

        # 添加时段
        for idx, seg in enumerate(data.time_segments):
            db.add(PlanTimeSegment(
                plan_id=plan.id,
                label=seg.label,
                start_time=seg.start_time,
                end_time=seg.end_time,
                sort_order=seg.sort_order or idx,
            ))

        await db.commit()
        return await cls.get_batch(db, plan.id)

    @classmethod
    async def update_batch(cls, db: AsyncSession, plan_id: int, data: BatchUpdate) -> BatchResponse:
        plan = await cls._get_plan(db, plan_id, for_update=True)
        if plan.status != "draft":
            raise BusinessException("仅草稿状态的批次可以编辑")
        _fs = getattr(data, "model_fields_set", set())
        cls._validate_schedule(
            apply_start=data.apply_start if "apply_start" in _fs or data.apply_start else plan.apply_start,
            apply_end=data.apply_end if "apply_end" in _fs or data.apply_end else plan.apply_end,
            exam_date=data.exam_date if "exam_date" in _fs or data.exam_date else plan.exam_date,
        )
        for field in (
            "name", "apply_start", "apply_end", "exam_date", "exam_location",
            "description", "contact_name", "contact_phone", "sort_order",
        ):
            value = getattr(data, field)
            # v1 兼容：显式 None 清除可选日期/描述字段
            explicitly_set = field in getattr(data, "model_fields_set", set())
            if value is not None or (explicitly_set and field in ("apply_start", "apply_end", "exam_date", "description", "exam_location", "contact_name", "contact_phone")):
                if field in ("apply_start", "apply_end", "exam_date"):
                    value = cls._normalize_datetime(value)
                setattr(plan, field, value)
        await db.commit()
        return await cls.get_batch(db, plan_id)

    @classmethod
    async def delete_batch(cls, db: AsyncSession, plan_id: int) -> None:
        plan = await cls._get_plan(db, plan_id, for_update=True)
        if plan.status != "draft":
            raise BusinessException("仅草稿状态的批次可以删除")
        order_count = (await db.execute(
            select(func.count()).select_from(Order).where(Order.plan_id == plan_id)
        )).scalar_one()
        if order_count > 0:
            raise BusinessException("批次下存在订单，无法删除")
        await db.delete(plan)
        await db.commit()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    @classmethod
    async def publish(cls, db: AsyncSession, plan_id: int) -> BatchResponse:
        plan = await cls._get_plan(db, plan_id, for_update=True)
        if plan.status != "draft":
            raise BusinessException("仅草稿状态的批次可以发布")
        products = await cls._get_products(db, plan_id)
        segments = await cls._get_segments(db, plan_id)
        if not products:
            raise ValidationException("批次至少包含一个产品才能发布")
        if not segments:
            raise ValidationException("批次至少包含一个考试时段才能发布")
        # v2: 校验所有产品仍可用
        for pp, cert in products:
            if not cert.is_active:
                raise BusinessException(f"批次包含已下架产品：{cert.name}，无法发布")
            if cert.type == "renshe" and cert.renshe_status not in (None, "available"):
                raise BusinessException(f"人社项目当前不可用：{cert.name}，无法发布")
        cls._validate_schedule(
            apply_start=plan.apply_start,
            apply_end=plan.apply_end,
            exam_date=plan.exam_date,
            require_window=True,
        )
        plan.status = "published"
        plan.published_at = datetime.now(timezone.utc)
        await db.commit()
        return await cls.get_batch(db, plan_id)

    @classmethod
    async def close_registration(cls, db_or_plan_id, plan_id: int | None = None,
                                 product_code: str | None = None, **kw) -> BatchResponse:
        """v2: close_registration(db, plan_id) / v1: close_registration(plan_id, product_code, ...)"""
        if isinstance(db_or_plan_id, AsyncSession):
            return await cls._close_registration_impl(db_or_plan_id, plan_id)
        async with get_db_ctx() as db:
            return await cls._close_registration_impl(db, db_or_plan_id)

    @classmethod
    async def _close_registration_impl(cls, db: AsyncSession, plan_id: int) -> BatchResponse:
        plan = await cls._get_plan(db, plan_id, for_update=True)
        if plan.status != "published":
            raise BusinessException("仅已发布的批次可以关闭报名")
        plan.status = "registration_closed"
        plan.registration_closed_at = datetime.now(timezone.utc)
        await db.commit()
        return await cls.get_batch(db, plan_id)

    @classmethod
    async def finalize(cls, db: AsyncSession, plan_id: int) -> BatchResponse:
        plan = await cls._get_plan(db, plan_id, for_update=True)
        if plan.status != "registration_closed":
            raise BusinessException("仅已关闭报名的批次可以终审归档")
        plan.status = "finalized"
        plan.finalized_at = datetime.now(timezone.utc)
        await db.commit()
        return await cls.get_batch(db, plan_id)

    @classmethod
    async def cancel(cls, db: AsyncSession, plan_id: int) -> BatchResponse:
        plan = await cls._get_plan(db, plan_id, for_update=True)
        if plan.status in ("finalized", "archived", "cancelled"):
            raise BusinessException("该批次当前状态不可取消")
        plan.status = "cancelled"
        plan.cancelled_at = datetime.now(timezone.utc)
        await db.commit()
        return await cls.get_batch(db, plan_id)

    @classmethod
    async def archive(cls, db: AsyncSession, plan_id: int) -> BatchResponse:
        plan = await cls._get_plan(db, plan_id, for_update=True)
        if plan.status not in ("finalized", "cancelled"):
            raise BusinessException("仅终审或取消的批次可以归档")
        plan.status = "archived"
        await db.commit()
        return await cls.get_batch(db, plan_id)

    # ------------------------------------------------------------------
    # 批次内产品管理
    # ------------------------------------------------------------------

    @classmethod
    async def _add_product_row(
        cls, db: AsyncSession, plan_id: int, data: PlanProductCreate
    ) -> PlanProduct:
        cert = (await db.execute(
            select(CertProduct).where(CertProduct.id == data.product_id).with_for_update()
        )).scalar_one_or_none()
        if cert is None:
            raise NotFoundException("认证产品")
        # v2: is_active=False 表示完全下架，不允许加入
        # pending/draft 状态允许加入但不允许发布（发布时严格校验）
        if cert.is_active is False:
            raise BusinessException("该认证产品未上架，不能加入批次")

        existing = await db.execute(
            select(PlanProduct.id).where(
                PlanProduct.plan_id == plan_id,
                PlanProduct.product_id == data.product_id,
            ).limit(1)
        )
        if existing.scalar_one_or_none() is not None:
            raise ConflictException("该产品已在此批次中")

        # 人社产品校验必填字段
        if cert.type == "renshe":
            missing = []
            if not data.renshe_config_id:
                missing.append("人社项目配置")
            for field, label in (
                ("occupation_name", "职业名称"),
                ("skill_level", "技能等级"),
                ("exam_type", "考试类型"),
                ("application_type", "申请类型"),
            ):
                if not getattr(data, field):
                    missing.append(label)
            if missing:
                raise ValidationException(f"人社产品缺少必填字段：{', '.join(missing)}")
            # 校验配置归属一致
            if data.renshe_config_id is not None:
                from app.models.renshe_project import RensheProjectConfig
                config = await db.get(RensheProjectConfig, data.renshe_config_id)
                if config is None:
                    raise BusinessException("人社项目配置不存在")
                if config.product_id != cert.id:
                    raise BusinessException("批次产品归属与配置不一致，归属不一致")

        pp = PlanProduct(
            plan_id=plan_id,
            product_id=data.product_id,
            capacity=data.capacity,
            renshe_config_id=data.renshe_config_id,
            occupation_name=data.occupation_name,
            occupation_code=data.occupation_code,
            skill_level=data.skill_level,
            exam_type=data.exam_type,
            application_type=data.application_type,
        )
        db.add(pp)
        await db.flush()
        return pp

    @classmethod
    async def add_product(cls, db: AsyncSession, plan_id: int, data: PlanProductCreate) -> BatchResponse:
        plan = await cls._get_plan(db, plan_id, for_update=True)
        if plan.status != "draft":
            raise BusinessException("仅草稿状态的批次可以添加产品")
        await cls._add_product_row(db, plan_id, data)
        await db.commit()
        return await cls.get_batch(db, plan_id)

    @classmethod
    async def update_product(
        cls, db: AsyncSession, plan_id: int, pp_id: int, data: PlanProductUpdate
    ) -> BatchResponse:
        plan = await cls._get_plan(db, plan_id, for_update=True)
        if plan.status != "draft":
            raise BusinessException("仅草稿状态的批次可以修改产品")
        pp = (await db.execute(
            select(PlanProduct).where(
                PlanProduct.id == pp_id, PlanProduct.plan_id == plan_id
            ).with_for_update()
        )).scalar_one_or_none()
        if pp is None:
            raise NotFoundException("批次产品")
        for field in (
            "capacity", "renshe_config_id", "occupation_name", "occupation_code",
            "skill_level", "exam_type", "application_type",
        ):
            value = getattr(data, field)
            if value is not None:
                setattr(pp, field, value)
        await db.commit()
        return await cls.get_batch(db, plan_id)

    @classmethod
    async def remove_product(cls, db: AsyncSession, plan_id: int, pp_id: int) -> BatchResponse:
        plan = await cls._get_plan(db, plan_id, for_update=True)
        if plan.status != "draft":
            raise BusinessException("仅草稿状态的批次可以移除产品")
        pp = (await db.execute(
            select(PlanProduct).where(
                PlanProduct.id == pp_id, PlanProduct.plan_id == plan_id
            ).with_for_update()
        )).scalar_one_or_none()
        if pp is None:
            raise NotFoundException("批次产品")
        # 检查是否有订单
        cert = (await db.execute(
            select(CertProduct).where(CertProduct.id == pp.product_id)
        )).scalar_one()
        order_count = (await db.execute(
            select(func.count()).select_from(Order).where(
                Order.plan_id == plan_id,
                Order.product_type == cert.code,
            )
        )).scalar_one()
        if order_count > 0:
            raise BusinessException("该产品在此批次下已有订单，无法移除")
        await db.delete(pp)
        await db.commit()
        return await cls.get_batch(db, plan_id)

    # ------------------------------------------------------------------
    # 批次内时段管理
    # ------------------------------------------------------------------

    @classmethod
    async def add_time_segment(
        cls, db: AsyncSession, plan_id: int, data: TimeSegmentCreate
    ) -> BatchResponse:
        plan = await cls._get_plan(db, plan_id, for_update=True)
        if plan.status != "draft":
            raise BusinessException("仅草稿状态的批次可以添加时段")
        if data.end_time <= data.start_time:
            raise ValidationException("时段结束时间必须晚于开始时间")
        db.add(PlanTimeSegment(
            plan_id=plan_id,
            label=data.label,
            start_time=data.start_time,
            end_time=data.end_time,
            sort_order=data.sort_order,
        ))
        await db.commit()
        return await cls.get_batch(db, plan_id)

    @classmethod
    async def update_time_segment(
        cls, db: AsyncSession, plan_id: int, ts_id: int, data: TimeSegmentUpdate
    ) -> BatchResponse:
        plan = await cls._get_plan(db, plan_id, for_update=True)
        if plan.status != "draft":
            raise BusinessException("仅草稿状态的批次可以修改时段")
        ts = (await db.execute(
            select(PlanTimeSegment).where(
                PlanTimeSegment.id == ts_id, PlanTimeSegment.plan_id == plan_id
            ).with_for_update()
        )).scalar_one_or_none()
        if ts is None:
            raise NotFoundException("考试时段")
        for field in ("label", "start_time", "end_time", "sort_order"):
            value = getattr(data, field)
            if value is not None:
                setattr(ts, field, value)
        if ts.end_time <= ts.start_time:
            raise ValidationException("时段结束时间必须晚于开始时间")
        await db.commit()
        return await cls.get_batch(db, plan_id)

    @classmethod
    async def remove_time_segment(
        cls, db: AsyncSession, plan_id: int, ts_id: int
    ) -> BatchResponse:
        plan = await cls._get_plan(db, plan_id, for_update=True)
        if plan.status != "draft":
            raise BusinessException("仅草稿状态的批次可以删除时段")
        ts = (await db.execute(
            select(PlanTimeSegment).where(
                PlanTimeSegment.id == ts_id, PlanTimeSegment.plan_id == plan_id
            ).with_for_update()
        )).scalar_one_or_none()
        if ts is None:
            raise NotFoundException("考试时段")
        await db.delete(ts)
        await db.commit()
        return await cls.get_batch(db, plan_id)

    # ------------------------------------------------------------------
    # 用户端：按产品查可报名批次
    # ------------------------------------------------------------------

    @classmethod
    async def list_published_batches_for_product(
        cls, db: AsyncSession, product_code: str
    ) -> list[BatchResponse]:
        cert = (await db.execute(
            select(CertProduct).where(CertProduct.code == product_code)
        )).scalar_one_or_none()
        if cert is None:
            raise NotFoundException("认证产品")

        plan_ids = (
            await db.execute(
                select(PlanProduct.plan_id)
                .where(PlanProduct.product_id == cert.id)
                .distinct()
            )
        ).scalars().all()
        if not plan_ids:
            return []

        plans = (await db.execute(
            select(Plan)
            .where(Plan.id.in_(plan_ids), Plan.status == "published")
            .order_by(Plan.sort_order, Plan.id.desc())
        )).scalars().all()

        now = datetime.now(timezone.utc)
        results: list[BatchResponse] = []
        for plan in plans:
            if plan.apply_start and cls._normalize_datetime(plan.apply_start) > now:
                continue
            if plan.apply_end and cls._normalize_datetime(plan.apply_end) < now:
                continue
            results.append(await cls.get_batch(db, plan.id))
        return results

    @classmethod
    async def list_user_batches_for_product(
        cls, db: AsyncSession, product_code: str
    ) -> list["UserBatchResponse"]:
        """用户端批次列表：字段白名单投影。"""
        from app.schemas.plan import UserBatchResponse, UserPlanProductResponse
        full_list = await cls.list_published_batches_for_product(db, product_code)
        return [
            UserBatchResponse(
                id=f.id, name=f.name, apply_start=f.apply_start, apply_end=f.apply_end,
                exam_date=f.exam_date, exam_location=f.exam_location, description=f.description,
                status=f.status, time_segments=f.time_segments, total_enrolled=f.total_enrolled,
                products=[
                    UserPlanProductResponse(
                        id=p.id, product_code=p.product_code, product_name=p.product_name,
                        product_type=p.product_type, capacity=p.capacity, enrolled=p.enrolled,
                        remaining=p.remaining, skill_level=p.skill_level, application_type=p.application_type,
                    ) for p in f.products
                ],
            ) for f in full_list
        ]

    # ------------------------------------------------------------------
    # v1 兼容方法（过渡期，测试与旧路由仍在使用）
    # ------------------------------------------------------------------

    async def create_plan(self, product_type: str, data) -> BatchResponse:
        """v1: PlanService().create_plan(pt, PlanCreate(...))"""
        from app.schemas.plan import BatchCreate, PlanProductCreate
        from app.models.cert_product import CertProduct
        async with get_db_ctx() as db:
            cert = (await db.execute(
                select(CertProduct).where(CertProduct.code == product_type)
            )).scalar_one_or_none()
            if cert is None:
                raise BusinessException(
                    f"认证产品 {product_type} 不存在；请先通过管理后台创建产品，"
                    "v2 不再自动创建占位产品"
                )
            batch_data = data if isinstance(data, BatchCreate) else BatchCreate.model_validate(
                data.model_dump(exclude={"product_code", "price_cents", "program_code", "project_config_id"}, exclude_none=True)
                if hasattr(data, "model_dump") else data
            )
            if not batch_data.products:
                # v1 PlanCreate 有 capacity，BatchCreate 没有 — 从原始 data 或 model_extra 提取
                capacity = getattr(data, "capacity", 0)
                if not capacity and hasattr(data, "model_extra") and data.model_extra:
                    capacity = data.model_extra.get("capacity", 0)
                if not capacity and hasattr(data, "__dict__"):
                    capacity = data.__dict__.get("capacity", 0)
                capacity = capacity or 0
                batch_data.products = [PlanProductCreate(product_id=cert.id, capacity=capacity)]
            return await self.create_batch(db, batch_data)

    async def list_plans(self, product_type: str) -> list[BatchResponse]:
        async with get_db_ctx() as db:
            # v1 兼容：返回该产品所有状态的批次（v2 版本只返回 published）
            cert = (await db.execute(
                select(CertProduct).where(CertProduct.code == product_type)
            )).scalar_one_or_none()
            if cert is None:
                return []
            plan_ids = (await db.execute(
                select(PlanProduct.plan_id).where(PlanProduct.product_id == cert.id).distinct()
            )).scalars().all()
            if not plan_ids:
                return []
            plans = (await db.execute(
                select(Plan).where(Plan.id.in_(plan_ids)).order_by(Plan.sort_order, Plan.id.desc())
            )).scalars().all()
            return [await self.get_batch(db, p.id) for p in plans]

    async def list_published_plans(self, product_type: str) -> list[BatchResponse]:
        async with get_db_ctx() as db:
            return await self.list_published_batches_for_product(db, product_type)

    async def list_renshe_plans(self) -> list[BatchResponse]:
        async with get_db_ctx() as db:
            return await self.list_published_batches_for_product(db, "renshe")

    async def get_plan(self, plan_id: int) -> BatchResponse:
        async with get_db_ctx() as db:
            return await self.get_batch(db, plan_id)

    async def publish_plan(self, plan_id: int, product_code: str | None = None, **kw) -> BatchResponse:
        async with get_db_ctx() as db:
            # v1 兼容：发布时自动补一个默认时段
            from datetime import time as _time
            existing = await db.scalar(select(PlanTimeSegment.id).where(PlanTimeSegment.plan_id == plan_id).limit(1))
            if existing is None:
                db.add(PlanTimeSegment(plan_id=plan_id, label="上午场", start_time=_time(9,0), end_time=_time(12,0)))
                await db.flush()
            return await self.publish(db, plan_id)

    async def update_plan(self, plan_id: int, data, product_code: str | None = None, **kw) -> BatchResponse:
        async with get_db_ctx() as db:
            # v1 兼容：显式 None 清除可选日期字段
            from app.schemas.plan import BatchUpdate
            update_kwargs = {}
            for field in ("name", "apply_start", "apply_end", "exam_date", "exam_location",
                          "description", "contact_name", "contact_phone", "sort_order"):
                if hasattr(data, field) and (getattr(data, field) is not None or field in getattr(data, "model_fields_set", set())):
                    update_kwargs[field] = getattr(data, field)
            return await self.update_batch(db, plan_id, BatchUpdate(**update_kwargs))

    async def delete_plan(self, plan_id: int) -> None:
        async with get_db_ctx() as db:
            return await self.delete_batch(db, plan_id)

    async def archive_plan(self, plan_id: int) -> BatchResponse:
        async with get_db_ctx() as db:
            # v1 兼容：published → archived 需经过 close → finalize
            plan = await db.scalar(select(Plan).where(Plan.id == plan_id).with_for_update())
            if plan is not None and plan.status == "published":
                await self.close_registration(db, plan_id)
                await self.finalize(db, plan_id)
            return await self.archive(db, plan_id)

    async def cancel_plan(self, plan_id: int) -> BatchResponse:
        async with get_db_ctx() as db:
            return await self.cancel(db, plan_id)

# ============================================================
# 兼容旧接口签名（过渡期，内部逐步替换）
# ============================================================

async def list_plans(product_type: str) -> list[BatchResponse]:
    async with get_db_ctx() as db:
        return await PlanService.list_published_batches_for_product(db, product_type)


async def get_plan(plan_id: int) -> BatchResponse:
    async with get_db_ctx() as db:
        return await PlanService.get_batch(db, plan_id)
