from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.domain.order.src.index import Order
from app.domain.plan.src.index import Plan
from app.domain.renshe.src.index import RensheApplication, RensheAuditLog
from app.port.config import settings
from app.port.exceptions import BusinessException, NotFoundException, ValidationException
from app.schemas.plan import (
    PlanCreate,
    PlanImpactAction,
    PlanImpactResponse,
    PlanResponse,
    PlanUpdate,
)
from app.services.plan_enrollment import CAPACITY_OCCUPYING_ORDER_STATUSES
from app.services.renshe_batch import RensheBatchService


RENSHE_FIXED_FIELDS = {
    "occupation_name": "信息安全管理员",
    "occupation_code": "4-04-04-02-网络与信息安全管理员-信息安全管理员-中级工",
    "skill_level": "中级工",
    "exam_type": "新考",
    "application_type": "学历型",
}
PUBLISHED_RENSHE_EDITABLE_FIELDS = {
    "apply_start",
    "apply_end",
    "exam_date",
    "capacity",
    "price_cents",
    "exam_location",
    "description",
    "contact_name",
    "contact_phone",
    "sort_order",
}


class PlanService:
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
            raise ValidationException("报名开始时间必须早于截止时间")
        if exam is not None and end is not None and exam < end:
            raise ValidationException("考试日期不能早于报名截止时间")
        if require_window and end is not None:
            current = cls._normalize_datetime(now or datetime.now(timezone.utc))
            if end <= current:
                raise ValidationException("报名截止时间必须晚于当前时间")

    @staticmethod
    def _validate_renshe_publish_fields(plan: Plan) -> None:
        missing = []
        for field, label in (
            ("exam_date", "考试时间"),
            ("exam_location", "考试地点"),
            ("description", "报名说明"),
            ("contact_name", "联系人"),
            ("contact_phone", "联系电话"),
        ):
            value = getattr(plan, field)
            if value is None or (isinstance(value, str) and not value.strip()):
                missing.append(label)
        if missing:
            raise ValidationException(f"发布人社批次前必须填写: {', '.join(missing)}")
        if plan.price_cents < 1:
            raise ValidationException("人社批次价格必须大于 0 分")

    async def list_plans(self, product_type: str) -> list[PlanResponse]:
        async with get_db_ctx() as db:
            rows = (
                await db.execute(
                    select(Plan)
                    .where(Plan.product_type == product_type)
                    .order_by(Plan.sort_order.desc(), Plan.id.desc())
                )
            ).scalars().all()
            enrolled_map = await self._batch_enrolled(db, [row.id for row in rows])
            return [self._response(row, enrolled_map.get(row.id, 0)) for row in rows]

    async def create_plan(
        self,
        product_type: str,
        data: PlanCreate,
        *,
        admin_id: int | None = None,
    ) -> PlanResponse:
        self._validate_schedule(
            apply_start=data.apply_start,
            apply_end=data.apply_end,
            exam_date=data.exam_date,
        )
        async with get_db_ctx() as db:
            existing = await db.scalar(
                select(Plan).where(
                    Plan.product_type == product_type,
                    Plan.name == data.name,
                )
            )
            if existing:
                raise BusinessException(f"该认证下已存在名为「{data.name}」的批次")
            plan = Plan(
                product_type=product_type,
                name=data.name,
                apply_start=self._normalize_datetime(data.apply_start),
                apply_end=self._normalize_datetime(data.apply_end),
                exam_date=self._normalize_datetime(data.exam_date),
                capacity=data.capacity,
                price_cents=data.price_cents,
                exam_location=data.exam_location,
                description=data.description,
                contact_name=data.contact_name,
                contact_phone=data.contact_phone,
                sort_order=data.sort_order,
                status="draft",
                **RENSHE_FIXED_FIELDS,
            )
            db.add(plan)
            await db.flush()
            self._add_audit(
                db,
                plan=plan,
                admin_id=admin_id,
                action="batch.create",
                summary={"product_type": product_type},
            )
            await db.commit()
            await db.refresh(plan)
            return self._response(plan, 0)

    async def update_plan(
        self,
        plan_id: int,
        data: PlanUpdate,
        product_type: str | None = None,
        *,
        admin_id: int | None = None,
    ) -> PlanResponse:
        async with get_db_ctx() as db:
            plan = await self._get_plan(db, plan_id, product_type, for_update=True)
            update_data = data.model_dump(exclude_unset=True)
            if plan.status == "published" and plan.product_type == "RS-ZY":
                forbidden = set(update_data) - PUBLISHED_RENSHE_EDITABLE_FIELDS
                if forbidden:
                    raise BusinessException("已发布人社批次不能修改批次名称或固定职业字段")
            elif plan.status != "draft":
                raise BusinessException("当前批次状态不可编辑")

            if "name" in update_data:
                if update_data["name"] is None:
                    raise ValidationException("批次名称不能为空")
                existing = await db.scalar(
                    select(Plan).where(
                        Plan.product_type == plan.product_type,
                        Plan.name == update_data["name"],
                        Plan.id != plan_id,
                    )
                )
                if existing:
                    raise BusinessException(
                        f"该认证下已存在名为「{update_data['name']}」的批次"
                    )

            self._validate_schedule(
                apply_start=update_data.get("apply_start", plan.apply_start),
                apply_end=update_data.get("apply_end", plan.apply_end),
                exam_date=update_data.get("exam_date", plan.exam_date),
                require_window=(plan.status == "published"),
            )
            changed_fields: list[str] = []
            for field in ("apply_start", "apply_end", "exam_date"):
                normalized = self._normalize_datetime(update_data.get(field))
                if field in update_data and getattr(plan, field) != normalized:
                    setattr(plan, field, normalized)
                    changed_fields.append(field)
            for field, value in update_data.items():
                if field in {"apply_start", "apply_end", "exam_date"}:
                    continue
                if value is not None and getattr(plan, field) != value:
                    setattr(plan, field, value)
                    changed_fields.append(field)
            if plan.status == "published" and plan.product_type == "RS-ZY":
                self._validate_renshe_publish_fields(plan)
            self._add_audit(
                db,
                plan=plan,
                admin_id=admin_id,
                action="batch.update",
                summary={"changed_fields": changed_fields},
            )
            await db.commit()
            await db.refresh(plan)
            return self._response(plan, await self._count_enrolled(db, plan_id))

    async def publish_plan(
        self,
        plan_id: int,
        product_type: str | None = None,
        *,
        admin_id: int | None = None,
    ) -> PlanResponse:
        async with get_db_ctx() as db:
            plan = await self._get_plan(db, plan_id, product_type, for_update=True)
            if plan.status != "draft":
                raise BusinessException("仅草稿状态的批次可发布")
            self._validate_schedule(
                apply_start=plan.apply_start,
                apply_end=plan.apply_end,
                exam_date=plan.exam_date,
                require_window=True,
            )
            if plan.product_type == "RS-ZY":
                self._validate_renshe_publish_fields(plan)
            plan.status = "published"
            plan.published_at = datetime.now(timezone.utc)
            self._add_audit(
                db,
                plan=plan,
                admin_id=admin_id,
                action="batch.publish",
                summary={},
            )
            await db.commit()
            await db.refresh(plan)
            return self._response(plan, await self._count_enrolled(db, plan_id))

    async def close_registration(
        self,
        plan_id: int,
        product_type: str | None = None,
        *,
        admin_id: int | None = None,
    ) -> PlanResponse:
        async with get_db_ctx() as db:
            plan = await self._get_plan(db, plan_id, product_type, for_update=True)
            if plan.status != "published":
                raise BusinessException("仅已发布批次可关闭报名")
            plan.status = "registration_closed"
            plan.registration_closed_at = datetime.now(timezone.utc)
            self._add_audit(
                db,
                plan=plan,
                admin_id=admin_id,
                action="batch.close_registration",
                summary={},
            )
            await db.commit()
            await db.refresh(plan)
            return self._response(plan, await self._count_enrolled(db, plan_id))

    async def finalize_plan(
        self,
        plan_id: int,
        product_type: str | None,
        *,
        admin_id: int,
    ) -> PlanResponse:
        async with get_db_ctx() as db:
            plan = await self._get_plan(db, plan_id, product_type, for_update=True)
            if plan.product_type != "RS-ZY":
                raise BusinessException("批次终结接口仅用于人社报名")
            await RensheBatchService().finalize(db, plan=plan, admin_id=admin_id)
            await db.commit()
            await db.refresh(plan)
            return self._response(plan, await self._count_enrolled(db, plan_id))

    async def preview_impact(
        self,
        plan_id: int,
        product_type: str | None,
        *,
        action: PlanImpactAction,
    ) -> PlanImpactResponse:
        async with get_db_ctx() as db:
            plan = await self._get_plan(db, plan_id, product_type)
            if plan.product_type != "RS-ZY":
                raise BusinessException("批次影响预览接口仅用于人社报名")
            return await RensheBatchService().preview_impact(
                db,
                plan=plan,
                action=action,
            )

    async def archive_plan(
        self,
        plan_id: int,
        product_type: str | None = None,
        *,
        admin_id: int | None = None,
    ) -> PlanResponse:
        async with get_db_ctx() as db:
            plan = await self._get_plan(db, plan_id, product_type, for_update=True)
            allowed = {"finalized", "cancelled"} if plan.product_type == "RS-ZY" else {"published"}
            if plan.status not in allowed:
                raise BusinessException("当前批次状态不可归档")
            plan.status = "archived"
            self._add_audit(
                db,
                plan=plan,
                admin_id=admin_id,
                action="batch.archive",
                summary={},
            )
            await db.commit()
            await db.refresh(plan)
            return self._response(plan, await self._count_enrolled(db, plan_id))

    async def cancel_plan(
        self,
        plan_id: int,
        product_type: str | None = None,
        *,
        admin_id: int | None = None,
    ) -> PlanResponse:
        async with get_db_ctx() as db:
            plan = await self._get_plan(db, plan_id, product_type, for_update=True)
            if plan.product_type == "RS-ZY":
                await RensheBatchService().cancel(db, plan=plan, admin_id=admin_id)
            else:
                if plan.status != "published":
                    raise BusinessException("仅已发布状态的批次可取消")
                plan.status = "cancelled"
            await db.commit()
            await db.refresh(plan)
            return self._response(plan, await self._count_enrolled(db, plan_id))

    async def delete_plan(
        self, plan_id: int, product_type: str | None = None
    ) -> None:
        async with get_db_ctx() as db:
            plan = await self._get_plan(db, plan_id, product_type, for_update=True)
            if plan.status != "draft":
                raise BusinessException("仅草稿状态的批次可删除")
            linked_order_id = await db.scalar(
                select(Order.id).where(Order.plan_id == plan_id).limit(1)
            )
            linked_application_id = await db.scalar(
                select(RensheApplication.id)
                .where(RensheApplication.plan_id == plan_id)
                .limit(1)
            )
            if linked_order_id is not None or linked_application_id is not None:
                raise BusinessException("已有报名或订单关联该批次，不能删除")
            await db.delete(plan)
            await db.commit()

    async def list_published_plans(self, product_type: str) -> list[PlanResponse]:
        async with get_db_ctx() as db:
            now = datetime.now(timezone.utc)
            rows = (
                await db.execute(
                    select(Plan)
                    .where(
                        Plan.product_type == product_type,
                        Plan.status == "published",
                        Plan.apply_start.is_not(None),
                        Plan.apply_end.is_not(None),
                        Plan.apply_start <= now,
                        Plan.apply_end >= now,
                    )
                    .order_by(Plan.sort_order.desc(), Plan.id.desc())
                )
            ).scalars().all()
            enrolled_map = await self._batch_enrolled(db, [row.id for row in rows])
            return [self._response(row, enrolled_map.get(row.id, 0)) for row in rows]

    async def get_plan(self, plan_id: int) -> PlanResponse:
        async with get_db_ctx() as db:
            plan = await self._get_plan(db, plan_id)
            if plan.status not in {
                "published",
                "registration_closed",
                "finalized",
                "archived",
                "cancelled",
            }:
                raise NotFoundException("批次")
            return self._response(plan, await self._count_enrolled(db, plan_id))

    async def _get_plan(
        self,
        db,
        plan_id: int,
        product_type: str | None = None,
        *,
        for_update: bool = False,
    ) -> Plan:
        stmt = select(Plan).where(Plan.id == plan_id)
        if for_update:
            stmt = stmt.with_for_update()
        plan = await db.scalar(stmt)
        if plan is None or (product_type is not None and plan.product_type != product_type):
            raise NotFoundException("批次")
        return plan

    async def _count_enrolled(self, db, plan_id: int) -> int:
        return (
            await db.scalar(
                select(func.count())
                .select_from(Order)
                .where(
                    Order.plan_id == plan_id,
                    Order.status.in_(CAPACITY_OCCUPYING_ORDER_STATUSES),
                )
            )
            or 0
        )

    async def _batch_enrolled(self, db, plan_ids: list[int]) -> dict[int, int]:
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

    @staticmethod
    def _response(plan: Plan, enrolled: int) -> PlanResponse:
        return PlanResponse(
            **{key: value for key, value in plan.__dict__.items() if not key.startswith("_")},
            enrolled=enrolled,
        )

    @staticmethod
    def _add_audit(
        db,
        *,
        plan: Plan,
        admin_id: int | None,
        action: str,
        summary: dict,
    ) -> None:
        if plan.product_type != "RS-ZY":
            return
        db.add(
            RensheAuditLog(
                actor_type="admin" if admin_id is not None else "system",
                actor_id=admin_id,
                action=action,
                object_type="plan",
                object_id=plan.id,
                result="succeeded",
                summary=summary,
            )
        )
