from datetime import datetime, timezone

from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.port.exceptions import NotFoundException, ValidationException
from app.domain.community.src.index import Conversation
from app.domain.order.src.index import Order
from app.domain.user.src.index import User, UserIdentity
from app.schemas.admin import (
    AdminIdentityReview,
    AdminUserConversationBrief,
    AdminUserFilter,
    AdminUserListItem,
    AdminUserOrderBrief,
    AdminUserUpdate,
)
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

    async def get_user_orders(self, user_id: int) -> list[AdminUserOrderBrief]:
        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None:
                raise NotFoundException("用户")
            stmt = select(Order).where(Order.user_id == user_id).order_by(Order.id.desc())
            result = await db.execute(stmt)
            return [AdminUserOrderBrief.model_validate(o) for o in result.scalars().all()]

    async def get_user_conversations(self, user_id: int) -> list[AdminUserConversationBrief]:
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
            return [AdminUserConversationBrief.model_validate(c) for c in result.scalars().all()]

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

    async def review_identity(self, user_id: int, data: AdminIdentityReview) -> dict:
        """审核用户实名认证"""
        if data.status not in ("verified", "rejected"):
            raise ValidationException("status 必须为 verified 或 rejected")

        async with get_db_ctx() as db:
            identity = (
                await db.execute(
                    select(UserIdentity).where(UserIdentity.user_id == user_id)
                )
            ).scalar_one_or_none()
            if identity is None:
                raise NotFoundException("实名认证信息")

            identity.status = data.status
            if data.status == "verified":
                identity.verified_at = datetime.now(timezone.utc).isoformat()
            else:
                identity.verified_at = None
            await db.commit()

        return {"user_id": user_id, "status": data.status, "comment": data.comment}

    async def get_user_profile(self, user_id: int) -> "UserProfileDetail":
        """管理员查看任一用户的完整个人资料"""
        from app.services.user import UserService
        return await UserService().get_profile(user_id)

    async def get_user_identity(self, user_id: int) -> "UserIdentityResponse":
        """管理员查看任一用户的实名认证详情（身份证号不脱敏）"""
        from app.schemas.user import UserIdentityResponse

        async with get_db_ctx() as db:
            identity = (
                await db.execute(
                    select(UserIdentity).where(UserIdentity.user_id == user_id)
                )
            ).scalar_one_or_none()
            if identity is None:
                raise NotFoundException("实名认证信息")

            return UserIdentityResponse(
                user_type=identity.user_type,
                real_name=identity.real_name,
                id_card_number=identity.id_card_number,  # 管理端不脱敏
                id_card_front_oss=identity.id_card_front_oss,
                id_card_back_oss=identity.id_card_back_oss,
                student_card_oss=identity.student_card_oss,
                status=identity.status,
                verified_at=identity.verified_at,
                created_at=identity.created_at.isoformat() if identity.created_at else "",
            )