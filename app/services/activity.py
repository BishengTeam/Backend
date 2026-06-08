import csv
import io

from datetime import datetime, timezone
from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.port.exceptions import BusinessException, NotFoundException
from app.domain.content.src.index import Activity, ActivityRegistration, ActivityReminder
from app.schemas.activity import ActivityResponse
from app.schemas.common import PaginatedData


class ActivityService:

    async def list_activities(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedData[ActivityResponse]:
        now = datetime.now(timezone.utc)
        stmt = select(Activity).where(
            Activity.is_active == True,
        )

        stmt = stmt.where((Activity.end_time == None) | (Activity.end_time > now))
        async with get_db_ctx() as db:
            total = (
                await db.execute(
                    select(func.count()).select_from(stmt.subquery())
                )
            ).scalar() or 0

            activities = (
                await db.execute(
                    stmt.order_by(Activity.start_time.desc().nullslast(), Activity.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()

        return PaginatedData[ActivityResponse](
            items=[ActivityResponse.model_validate(a) for a in activities],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def register(
        self,
        *,
        activity_id: int,
        user_id: int,
        name: str,
        phone: str,
        remark: str | None = None,
    ) -> ActivityRegistration:
        async with get_db_ctx() as db:
            activity = await db.get(Activity, activity_id)
            if activity is None:
                raise NotFoundException("活动")

            if not activity.is_active:
                raise BusinessException("活动已下线，无法报名")

            if activity.end_time is not None and activity.end_time <= datetime.now(timezone.utc):
                raise BusinessException("活动已结束，无法报名")

            if activity.max_participants > 0:
                count = (
                    await db.execute(
                        select(func.count()).where(
                            ActivityRegistration.activity_id == activity_id
                        )
                    )
                ).scalar() or 0
                if count >= activity.max_participants:
                    raise BusinessException("活动报名人数已满")

            registration = await db.execute(
                select(ActivityRegistration).where(
                    ActivityRegistration.activity_id == activity_id,
                    ActivityRegistration.user_id == user_id,
                )
            )
            existing = registration.scalar_one_or_none()
            if existing is not None:
                raise BusinessException("您已报名该活动")

            reg = ActivityRegistration(
                activity_id=activity_id,
                user_id=user_id,
                name=name,
                phone=phone,
                remark=remark,
            )
            db.add(reg)
            await db.commit()
            await db.refresh(reg)
            return reg

    async def export_csv(self) -> str:
        """导出所有活动报名为 CSV（管理端）"""
        async with get_db_ctx() as db:
            result = await db.execute(
                select(ActivityRegistration).order_by(ActivityRegistration.id)
            )
            registrations = result.scalars().all()

            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["ID", "活动ID", "用户ID", "姓名", "电话", "备注", "报名时间"])
            for reg in registrations:
                writer.writerow([
                    reg.id,
                    reg.activity_id,
                    reg.user_id,
                    reg.name,
                    reg.phone,
                    reg.remark or "",
                    reg.created_at.isoformat() if reg.created_at else "",
                ])

            return output.getvalue()

    async def export_my_registrations(self, *, user_id: int) -> str:
        async with get_db_ctx() as db:
            result = await db.execute(
                select(ActivityRegistration)
                .where(ActivityRegistration.user_id == user_id)
                .order_by(ActivityRegistration.id)
            )
            registrations = result.scalars().all()

            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["ID", "活动ID", "用户ID", "姓名", "电话", "备注", "报名时间"])
            for reg in registrations:
                writer.writerow([
                    reg.id,
                    reg.activity_id,
                    reg.user_id,
                    reg.name,
                    reg.phone,
                    reg.remark or "",
                    reg.created_at.isoformat() if reg.created_at else "",
                ])

            return output.getvalue()

    async def set_reminder(self, user_id: int, activity_id: int) -> ActivityReminder:
        async with get_db_ctx() as db:
            activity = await db.get(Activity, activity_id)
            if activity is None:
                raise NotFoundException("活动")
            if not activity.is_active:
                raise BusinessException("活动已下线")

            existing = (
                await db.execute(
                    select(ActivityReminder).where(
                        ActivityReminder.user_id == user_id,
                        ActivityReminder.activity_id == activity_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                # 已设置提醒，直接返回
                return existing

            reminder = ActivityReminder(user_id=user_id, activity_id=activity_id)
            db.add(reminder)
            await db.commit()
            await db.refresh(reminder)
            return reminder