from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.port.exceptions import NotFoundException
from app.models.conversation import Conversation
from app.models.order import Order
from app.models.user import User
from app.schemas.admin import AdminUserFilter, AdminUserListItem, AdminUserUpdate
from app.schemas.common import PaginatedData


class AdminUserService:

    async def list_users(
        self, filters: AdminUserFilter | None, page: int, page_size: int
    ) -> PaginatedData[AdminUserListItem]:
        async with get_db_ctx() as db:
            base = select(User).where(User.is_deleted == False)
            if filters:
                if filters.openid:
                    base = base.where(User.openid == filters.openid)
                if filters.phone:
                    base = base.where(User.phone == filters.phone)
                if filters.created_at_start:
                    base = base.where(User.created_at >= filters.created_at_start)
                if filters.created_at_end:
                    base = base.where(User.created_at <= filters.created_at_end)
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

    async def toggle_user_status(self, user_id: int, is_active: bool) -> AdminUserListItem:
        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None:
                raise NotFoundException("用户")
            user.is_active = is_active
            await db.commit()
            await db.refresh(user)
            return AdminUserListItem.model_validate(user)

    async def batch_delete(self, user_ids: list[int]) -> int:
        async with get_db_ctx() as db:
            result = await db.execute(
                select(User).where(User.id.in_(user_ids), User.is_deleted == False)
            )
            users = result.scalars().all()
            for user in users:
                user.is_deleted = True
            await db.commit()
            return len(users)

    async def get_user_orders(self, user_id: int) -> list[dict]:
        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None:
                raise NotFoundException("用户")
            stmt = select(Order).where(Order.user_id == user_id).order_by(Order.id.desc())
            result = await db.execute(stmt)
            orders = result.scalars().all()
            return [
                {
                    "id": o.id,
                    "cert_type": o.cert_type,
                    "candidate_name": o.candidate_name,
                    "price": o.price,
                    "status": o.status,
                    "created_at": o.created_at,
                }
                for o in orders
            ]

    async def get_user_conversations(self, user_id: int) -> list[dict]:
        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None:
                raise NotFoundException("用户")
            stmt = (
                select(Conversation)
                .where(Conversation.user_id == user_id)
                .order_by(Conversation.id.desc())
            )
            result = await db.execute(stmt)
            conversations = result.scalars().all()
            return [
                {
                    "id": c.id,
                    "session_id": c.session_id,
                    "backend_type": c.backend_type,
                    "created_at": c.created_at,
                }
                for c in conversations
            ]

    async def export_users(self, filters: AdminUserFilter | None) -> str:
        import csv
        import io

        async with get_db_ctx() as db:
            base = select(User).where(User.is_deleted == False)
            if filters:
                if filters.openid:
                    base = base.where(User.openid == filters.openid)
                if filters.phone:
                    base = base.where(User.phone == filters.phone)
                if filters.created_at_start:
                    base = base.where(User.created_at >= filters.created_at_start)
                if filters.created_at_end:
                    base = base.where(User.created_at <= filters.created_at_end)
            stmt = base.order_by(User.id.desc())
            result = await db.execute(stmt)
            users = result.scalars().all()
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["ID", "OpenID", "Phone", "IsActive", "CreatedAt"])
            for u in users:
                writer.writerow([u.id, u.openid, u.phone or "", u.is_active, u.created_at])
            return output.getvalue()
