from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.port.exceptions import NotFoundException
from app.domain.content.src.index import Activity
from app.schemas.admin_activity import AdminActivityCreate, AdminActivityListItem, AdminActivityUpdate
from app.schemas.common import PaginatedData


class AdminActivityService:

    _list_columns = (
        Activity.id,
        Activity.title,
        Activity.cover_url,
        Activity.location,
        Activity.start_time,
        Activity.end_time,
        Activity.max_participants,
        Activity.is_active,
        Activity.created_at,
    )

    async def list_activities(
        self, keyword: str | None, page: int, page_size: int
    ) -> PaginatedData[AdminActivityListItem]:
        async with get_db_ctx() as db:
            base = select(*self._list_columns)
            if keyword:
                base = base.where(Activity.title.ilike(f"%{keyword}%"))
            count_stmt = select(func.count()).select_from(base.subquery())
            total = (await db.execute(count_stmt)).scalar() or 0
            stmt = base.order_by(Activity.id.desc()).offset((page - 1) * page_size).limit(page_size)
            result = await db.execute(stmt)
            items = result.all()
            return PaginatedData[AdminActivityListItem](
                items=[AdminActivityListItem.model_validate(a) for a in items],
                total=total,
                page=page,
                page_size=page_size,
            )

    async def create(self, data: AdminActivityCreate) -> AdminActivityListItem:
        async with get_db_ctx() as db:
            activity = Activity(**data.model_dump())
            db.add(activity)
            await db.commit()
            await db.refresh(activity)
            return AdminActivityListItem.model_validate(activity)

    async def update(self, activity_id: int, data: AdminActivityUpdate) -> AdminActivityListItem:
        async with get_db_ctx() as db:
            activity = await db.get(Activity, activity_id)
            if activity is None:
                raise NotFoundException("培训活动")
            update_data = data.model_dump(exclude_unset=True)
            for key, value in update_data.items():
                setattr(activity, key, value)
            await db.commit()
            await db.refresh(activity)
            return AdminActivityListItem.model_validate(activity)

    async def deactivate(self, activity_id: int) -> None:
        async with get_db_ctx() as db:
            activity = await db.get(Activity, activity_id)
            if activity is None:
                raise NotFoundException("培训活动")
            activity.is_active = False
            await db.commit()
