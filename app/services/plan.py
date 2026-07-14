from datetime import datetime, timezone

from sqlalchemy import select, func

from app.adapter.database import get_db_ctx
from app.domain.plan.src.index import Plan
from app.domain.order.src.index import Order
from app.port.exceptions import NotFoundException, BusinessException, ValidationException
from app.schemas.plan import PlanCreate, PlanUpdate, PlanResponse
from app.services.plan_enrollment import CAPACITY_OCCUPYING_ORDER_STATUSES


class PlanService:

    @staticmethod
    def _normalize_datetime(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
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
            raise ValidationException("报名开始时间必须早于截止时间")
        if exam is not None and end is not None and exam < end:
            raise ValidationException("考试日期不能早于报名截止时间")
        if require_window and end is not None:
            current = cls._normalize_datetime(now or datetime.now(timezone.utc))
            if end <= current:
                raise ValidationException("报名截止时间必须晚于当前时间")

    # ============== 管理端 ==============

    async def list_plans(self, product_type: str) -> list[PlanResponse]:
        """某认证下的所有批次列表"""
        async with get_db_ctx() as db:
            stmt = (
                select(Plan)
                .where(Plan.product_type == product_type)
                .order_by(Plan.id.desc())
            )
            rows = (await db.execute(stmt)).scalars().all()
            # 批量查询 enrolled count
            plan_ids = [r.id for r in rows]
            enrolled_map = await self._batch_enrolled(db, plan_ids)
            return [
                PlanResponse(
                    **{k: v for k, v in r.__dict__.items() if not k.startswith("_")},
                    enrolled=enrolled_map.get(r.id, 0),
                )
                for r in rows
            ]

    async def create_plan(self, product_type: str, data: PlanCreate) -> PlanResponse:
        """创建批次（draft）"""
        self._validate_schedule(
            apply_start=data.apply_start,
            apply_end=data.apply_end,
            exam_date=data.exam_date,
        )

        async with get_db_ctx() as db:
            # 检查名称唯一性
            existing = (await db.execute(
                select(Plan).where(Plan.product_type == product_type, Plan.name == data.name)
            )).scalar_one_or_none()
            if existing:
                raise BusinessException(f"该认证下已存在名为「{data.name}」的批次")

            plan = Plan(
                product_type=product_type,
                name=data.name,
                apply_start=data.apply_start,
                apply_end=data.apply_end,
                exam_date=data.exam_date,
                capacity=data.capacity,
                status="draft",
            )
            db.add(plan)
            await db.commit()
            await db.refresh(plan)
            return PlanResponse(
                **{k: v for k, v in plan.__dict__.items() if not k.startswith("_")},
                enrolled=0,
            )

    async def update_plan(
        self,
        plan_id: int,
        data: PlanUpdate,
        product_type: str | None = None,
    ) -> PlanResponse:
        """编辑草稿批次"""
        async with get_db_ctx() as db:
            plan = await self._get_plan(db, plan_id, product_type)
            if plan.status != "draft":
                raise BusinessException("仅草稿状态的批次可编辑")

            update_data = data.model_dump(exclude_unset=True)
            if "name" in update_data:
                if update_data["name"] is None:
                    raise ValidationException("批次名称不能为空")
                existing = (await db.execute(
                    select(Plan).where(
                        Plan.product_type == plan.product_type,
                        Plan.name == update_data["name"],
                        Plan.id != plan_id,
                    )
                )).scalar_one_or_none()
                if existing:
                    raise BusinessException(f"该认证下已存在名为「{update_data['name']}」的批次")

            next_start = update_data.get("apply_start", plan.apply_start)
            next_end = update_data.get("apply_end", plan.apply_end)
            next_exam = update_data.get("exam_date", plan.exam_date)
            self._validate_schedule(
                apply_start=next_start,
                apply_end=next_end,
                exam_date=next_exam,
            )

            if "name" in update_data:
                plan.name = update_data["name"]
            for field in ("apply_start", "apply_end", "exam_date"):
                if field in update_data:
                    setattr(plan, field, update_data[field])
            if "capacity" in update_data and update_data["capacity"] is not None:
                plan.capacity = update_data["capacity"]
            await db.commit()
            await db.refresh(plan)
            enrolled = await self._count_enrolled(db, plan_id)
            return PlanResponse(
                **{k: v for k, v in plan.__dict__.items() if not k.startswith("_")},
                enrolled=enrolled,
            )

    async def publish_plan(
        self,
        plan_id: int,
        product_type: str | None = None,
    ) -> PlanResponse:
        """发布批次"""
        async with get_db_ctx() as db:
            plan = await self._get_plan(db, plan_id, product_type)
            if plan.status != "draft":
                raise BusinessException("仅草稿状态的批次可发布")
            self._validate_schedule(
                apply_start=plan.apply_start,
                apply_end=plan.apply_end,
                exam_date=plan.exam_date,
                require_window=True,
            )
            plan.status = "published"
            await db.commit()
            await db.refresh(plan)
            enrolled = await self._count_enrolled(db, plan_id)
            return PlanResponse(
                **{k: v for k, v in plan.__dict__.items() if not k.startswith("_")},
                enrolled=enrolled,
            )

    async def archive_plan(
        self,
        plan_id: int,
        product_type: str | None = None,
    ) -> PlanResponse:
        """归档批次"""
        async with get_db_ctx() as db:
            plan = await self._get_plan(db, plan_id, product_type)
            if plan.status != "published":
                raise BusinessException("仅已发布状态的批次可归档")
            plan.status = "archived"
            await db.commit()
            await db.refresh(plan)
            enrolled = await self._count_enrolled(db, plan_id)
            return PlanResponse(
                **{k: v for k, v in plan.__dict__.items() if not k.startswith("_")},
                enrolled=enrolled,
            )

    async def cancel_plan(
        self,
        plan_id: int,
        product_type: str | None = None,
    ) -> PlanResponse:
        """取消批次（仅 published 状态可取消）"""
        async with get_db_ctx() as db:
            plan = await self._get_plan(db, plan_id, product_type)
            if plan.status != "published":
                raise BusinessException("仅已发布状态的批次可取消")
            plan.status = "cancelled"
            await db.commit()
            await db.refresh(plan)
            enrolled = await self._count_enrolled(db, plan_id)
            return PlanResponse(
                **{k: v for k, v in plan.__dict__.items() if not k.startswith("_")},
                enrolled=enrolled,
            )

    async def delete_plan(
        self,
        plan_id: int,
        product_type: str | None = None,
    ) -> None:
        """删除草稿批次"""
        async with get_db_ctx() as db:
            plan = await self._get_plan(db, plan_id, product_type)
            if plan.status != "draft":
                raise BusinessException("仅草稿状态的批次可删除")
            linked_order_id = await db.scalar(
                select(Order.id).where(Order.plan_id == plan_id).limit(1)
            )
            if linked_order_id is not None:
                raise BusinessException("已有订单关联该批次，不能删除")
            await db.delete(plan)
            await db.commit()

    # ============== 用户端 ==============

    async def list_published_plans(self, product_type: str) -> list[PlanResponse]:
        """用户端：某认证下当前处于报名窗口的已发布批次列表"""
        async with get_db_ctx() as db:
            now = datetime.now(timezone.utc)
            stmt = (
                select(Plan)
                .where(
                    Plan.product_type == product_type,
                    Plan.status == "published",
                    Plan.apply_start.is_not(None),
                    Plan.apply_end.is_not(None),
                    Plan.apply_start <= now,
                    Plan.apply_end >= now,
                )
                .order_by(Plan.id.desc())
            )
            rows = (await db.execute(stmt)).scalars().all()
            plan_ids = [r.id for r in rows]
            enrolled_map = await self._batch_enrolled(db, plan_ids)
            return [
                PlanResponse(
                    **{k: v for k, v in r.__dict__.items() if not k.startswith("_")},
                    enrolled=enrolled_map.get(r.id, 0),
                )
                for r in rows
            ]

    async def get_plan(self, plan_id: int) -> PlanResponse:
        """用户端：批次详情"""
        async with get_db_ctx() as db:
            plan = await self._get_plan(db, plan_id)
            if plan.status not in ("published", "archived"):
                raise NotFoundException("批次")
            enrolled = await self._count_enrolled(db, plan_id)
            return PlanResponse(
                **{k: v for k, v in plan.__dict__.items() if not k.startswith("_")},
                enrolled=enrolled,
            )

    # ============== 内部 ==============

    async def _get_plan(
        self,
        db,
        plan_id: int,
        product_type: str | None = None,
    ) -> Plan:
        plan = await db.get(Plan, plan_id)
        if plan is None or (product_type is not None and plan.product_type != product_type):
            raise NotFoundException("批次")
        return plan

    async def _count_enrolled(self, db, plan_id: int) -> int:
        """统计当前占用批次名额的订单数。"""
        result = await db.execute(
            select(func.count()).select_from(Order).where(
                Order.plan_id == plan_id,
                Order.status.in_(CAPACITY_OCCUPYING_ORDER_STATUSES),
            )
        )
        return result.scalar() or 0

    async def _batch_enrolled(self, db, plan_ids: list[int]) -> dict[int, int]:
        """批量统计当前占用批次名额的订单数。"""
        if not plan_ids:
            return {}
        result = await db.execute(
            select(Order.plan_id, func.count())
            .where(
                Order.plan_id.in_(plan_ids),
                Order.status.in_(CAPACITY_OCCUPYING_ORDER_STATUSES),
            )
            .group_by(Order.plan_id)
        )
        return {row[0]: row[1] for row in result.all()}
