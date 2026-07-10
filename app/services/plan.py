from sqlalchemy import select, func

from app.adapter.database import get_db_ctx
from app.domain.plan.src.index import Plan
from app.domain.order.src.index import Order
from app.port.exceptions import NotFoundException, BusinessException, ValidationException
from app.schemas.plan import PlanCreate, PlanUpdate, PlanResponse


class PlanService:

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
        if data.apply_start and data.apply_end and data.apply_start >= data.apply_end:
            raise ValidationException("报名开始时间必须早于截止时间")

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

    async def update_plan(self, plan_id: int, data: PlanUpdate) -> PlanResponse:
        """编辑草稿批次"""
        async with get_db_ctx() as db:
            plan = await self._get_plan(db, plan_id)
            if plan.status != "draft":
                raise BusinessException("仅草稿状态的批次可编辑")
            if data.name is not None:
                existing = (await db.execute(
                    select(Plan).where(
                        Plan.product_type == plan.product_type,
                        Plan.name == data.name,
                        Plan.id != plan_id,
                    )
                )).scalar_one_or_none()
                if existing:
                    raise BusinessException(f"该认证下已存在名为「{data.name}」的批次")
                plan.name = data.name
            for field in ("apply_start", "apply_end", "exam_date", "capacity"):
                if getattr(data, field) is not None:
                    setattr(plan, field, getattr(data, field))
            await db.commit()
            await db.refresh(plan)
            enrolled = await self._count_enrolled(db, plan_id)
            return PlanResponse(
                **{k: v for k, v in plan.__dict__.items() if not k.startswith("_")},
                enrolled=enrolled,
            )

    async def publish_plan(self, plan_id: int) -> PlanResponse:
        """发布批次"""
        async with get_db_ctx() as db:
            plan = await self._get_plan(db, plan_id)
            if plan.status != "draft":
                raise BusinessException("仅草稿状态的批次可发布")
            plan.status = "published"
            await db.commit()
            await db.refresh(plan)
            enrolled = await self._count_enrolled(db, plan_id)
            return PlanResponse(
                **{k: v for k, v in plan.__dict__.items() if not k.startswith("_")},
                enrolled=enrolled,
            )

    async def archive_plan(self, plan_id: int) -> PlanResponse:
        """归档批次"""
        async with get_db_ctx() as db:
            plan = await self._get_plan(db, plan_id)
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

    async def cancel_plan(self, plan_id: int) -> PlanResponse:
        """取消批次（仅 published 状态可取消）"""
        async with get_db_ctx() as db:
            plan = await self._get_plan(db, plan_id)
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

    async def delete_plan(self, plan_id: int) -> None:
        """删除草稿批次"""
        async with get_db_ctx() as db:
            plan = await self._get_plan(db, plan_id)
            if plan.status != "draft":
                raise BusinessException("仅草稿状态的批次可删除")
            await db.delete(plan)
            await db.commit()

    # ============== 用户端 ==============

    async def list_published_plans(self, product_type: str) -> list[PlanResponse]:
        """用户端：某认证下已发布的批次列表"""
        async with get_db_ctx() as db:
            stmt = (
                select(Plan)
                .where(Plan.product_type == product_type, Plan.status == "published")
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

    async def _get_plan(self, db, plan_id: int) -> Plan:
        plan = await db.get(Plan, plan_id)
        if plan is None:
            raise NotFoundException("批次")
        return plan

    async def _count_enrolled(self, db, plan_id: int) -> int:
        """统计已报名人数。Order.plan_id 字段待加，暂时 try/except 返回 0。"""
        try:
            result = await db.execute(
                select(func.count()).select_from(Order).where(
                    Order.plan_id == plan_id,
                    Order.status.in_(["paid", "completed"]),
                )
            )
            return result.scalar() or 0
        except Exception:
            return 0

    async def _batch_enrolled(self, db, plan_ids: list[int]) -> dict[int, int]:
        """批量统计已报名人数。Order.plan_id 字段待加，暂时 try/except 返回空。"""
        if not plan_ids:
            return {}
        try:
            result = await db.execute(
                select(Order.plan_id, func.count())
                .where(Order.plan_id.in_(plan_ids), Order.status.in_(["paid", "completed"]))
                .group_by(Order.plan_id)
            )
            return {row[0]: row[1] for row in result.all()}
        except Exception:
            return {}
