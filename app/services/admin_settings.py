from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.port.exceptions import BusinessException, ConflictException, NotFoundException
from app.domain.user.src.index import AdminUser, ADMIN_ROLES
from app.domain.renshe.src.index import RensheAuditLog
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

    async def create_admin(
        self, data: AdminSettingsUserCreate, *, actor_id: int | None = None
    ) -> AdminSettingsUserListItem:
        async with get_db_ctx() as db:
            existing = await db.scalar(
                select(AdminUser.id).where(AdminUser.username == data.username).limit(1)
            )
            if existing is not None:
                raise ConflictException("管理员用户名已存在")
            password_hash = AdminAuthService.hash_password(data.password)
            admin = AdminUser(
                username=data.username,
                password_hash=password_hash,
                role=data.role,
            )
            db.add(admin)
            await db.flush()
            db.add(
                RensheAuditLog(
                    actor_type="admin",
                    actor_id=actor_id,
                    action="admin_account.create",
                    object_type="admin_user",
                    object_id=admin.id,
                    result="succeeded",
                    summary={"role": data.role},
                )
            )
            await db.commit()
            await db.refresh(admin)
            return AdminSettingsUserListItem.model_validate(admin)

    async def update_admin(
        self,
        admin_id: int,
        data: AdminSettingsUserUpdate,
        *,
        actor_id: int | None = None,
    ) -> AdminSettingsUserListItem:
        async with get_db_ctx() as db:
            admin = await db.get(AdminUser, admin_id)
            if admin is None:
                raise NotFoundException("管理员")
            if admin.role == "super_admin":
                raise BusinessException("不能通过管理员列表修改唯一超级管理员")
            update_data = data.model_dump(exclude_unset=True)
            for key, value in update_data.items():
                setattr(admin, key, value)
            db.add(
                RensheAuditLog(
                    actor_type="admin",
                    actor_id=actor_id,
                    action="admin_account.update",
                    object_type="admin_user",
                    object_id=admin.id,
                    result="succeeded",
                    summary={"changed_fields": sorted(update_data)},
                )
            )
            await db.commit()
            await db.refresh(admin)
            return AdminSettingsUserListItem.model_validate(admin)

    async def reset_password(
        self, admin_id: int, password: str, *, actor_id: int | None = None
    ) -> None:
        async with get_db_ctx() as db:
            admin = await db.get(AdminUser, admin_id)
            if admin is None:
                raise NotFoundException("管理员")
            if admin.role == "super_admin":
                raise BusinessException("不能通过管理员列表重置唯一超级管理员")
            admin.password_hash = AdminAuthService.hash_password(password)
            db.add(
                RensheAuditLog(
                    actor_type="admin",
                    actor_id=actor_id,
                    action="admin_account.password_reset",
                    object_type="admin_user",
                    object_id=admin.id,
                    result="succeeded",
                    summary={},
                )
            )
            await db.commit()
