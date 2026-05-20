from sqlalchemy import func, select

from app.core.database import get_db_ctx
from app.core.exceptions import NotFoundException
from app.models.user import User
from app.schemas.admin import AdminUserFilter, AdminUserListItem, AdminUserUpdate
from app.schemas.common import PaginatedData


class AdminUserService:

    async def list_users(
        self, filters: AdminUserFilter | None, page: int, page_size: int
    ) -> PaginatedData[AdminUserListItem]:
        async with get_db_ctx() as db:
            base = select(User)
            if filters:
                if filters.openid:
                    base = base.where(User.openid == filters.openid)
                if filters.phone:
                    base = base.where(User.phone == filters.phone)
            count_stmt = select(func.count()).select_from(base.subquery())
            total = (await db.execute(count_stmt)).scalar() or 0
            stmt = base.order_by(User.id.desc()).offset((page - 1) * page_size).limit(page_size)
            result = await db.execute(stmt)
            users = result.scalars().all()
            return PaginatedData[AdminUserListItem](
                items=[AdminUserListItem.model_validate(u) for u in users],
                total=total,
                page=page,
                page_size=page_size,
            )

    async def get_user(self, user_id: int) -> AdminUserListItem:
        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None:
                raise NotFoundException("用户")
            return AdminUserListItem.model_validate(user)

    async def update_user(self, user_id: int, data: AdminUserUpdate) -> AdminUserListItem:
        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None:
                raise NotFoundException("用户")
            user.is_active = data.is_active
            await db.commit()
            await db.refresh(user)
            return AdminUserListItem.model_validate(user)
