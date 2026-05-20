from sqlalchemy import func, select

from app.core.database import get_db_ctx
from app.core.exceptions import NotFoundException
from app.models.admin_user import AdminUser, ADMIN_ROLES
from app.schemas.admin_settings import AdminSettingsUserCreate, AdminSettingsUserListItem, AdminSettingsUserUpdate
from app.schemas.common import PaginatedData
from app.services.admin_auth import AdminAuthService


class AdminSettingsService:

    async def list_admins(
        self, page: int, page_size: int
    ) -> PaginatedData[AdminSettingsUserListItem]:
        async with get_db_ctx() as db:
            base = select(AdminUser)
            count_stmt = select(func.count()).select_from(base.subquery())
            total = (await db.execute(count_stmt)).scalar() or 0
            stmt = base.order_by(AdminUser.id).offset((page - 1) * page_size).limit(page_size)
            result = await db.execute(stmt)
            admins = result.scalars().all()
            return PaginatedData[AdminSettingsUserListItem](
                items=[AdminSettingsUserListItem.model_validate(a) for a in admins],
                total=total,
                page=page,
                page_size=page_size,
            )

    async def create_admin(self, data: AdminSettingsUserCreate) -> AdminSettingsUserListItem:
        async with get_db_ctx() as db:
            password_hash = AdminAuthService.hash_password(data.password)
            admin = AdminUser(
                username=data.username,
                password_hash=password_hash,
                role=data.role,
            )
            db.add(admin)
            await db.commit()
            await db.refresh(admin)
            return AdminSettingsUserListItem.model_validate(admin)

    async def update_admin(self, admin_id: int, data: AdminSettingsUserUpdate) -> AdminSettingsUserListItem:
        async with get_db_ctx() as db:
            admin = await db.get(AdminUser, admin_id)
            if admin is None:
                raise NotFoundException("管理员")
            update_data = data.model_dump(exclude_unset=True)
            for key, value in update_data.items():
                setattr(admin, key, value)
            await db.commit()
            await db.refresh(admin)
            return AdminSettingsUserListItem.model_validate(admin)
