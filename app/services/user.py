from sqlalchemy import select, update

from app.core.database import get_db_ctx
from app.core.exceptions import BusinessException, NotFoundException, ValidationException
from app.core.redis import redis_client
from app.integrations.wechat import WechatClient
from app.models.deleted_openid import DeletedOpenid
from app.models.order import Order
from app.models.user import User
from app.models.user_identity import UserIdentity
from app.schemas.user import (
    UserIdentityCreate,
    UserIdentityResponse,
)
from app.utils.validators import validate_id_card

SESSION_KEY_PREFIX = "session_key:"
MAX_IDENTITY_EDITS = 3

def _mask_identity(identity: UserIdentity) -> UserIdentityResponse:
    id_card = identity.id_card_number
    masked = id_card[:4] + "**********" + id_card[-4:] if len(id_card) == 18 else "****"
    return UserIdentityResponse(
        user_type=identity.user_type,
        real_name=identity.real_name,
        id_card_number=masked,
        id_card_front_oss=identity.id_card_front_oss,
        id_card_back_oss=identity.id_card_back_oss,
        student_card_oss=identity.student_card_oss,
        status=identity.status,
        verified_at=identity.verified_at,
        created_at=identity.created_at.isoformat() if identity.created_at else "",
    )


class UserService:

    async def delete_account(self, user_id: int) -> None:
        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None:
                raise NotFoundException("用户")
            db.add(DeletedOpenid(openid=user.openid))
            await db.execute(
                update(Order)
                .where(Order.user_id == user.id)
                .values(candidate_name="***", candidate_phone="***", candidate_idcard="***")
            )
            user.is_active = False
            await db.commit()

    async def submit_identity(self, user_id: int, data: UserIdentityCreate) -> UserIdentityResponse:
        error = validate_id_card(data.id_card_number)
        if error:
            raise ValidationException(error)
        if data.user_type == "student" and not data.student_card_oss:
            raise ValidationException("学生用户必须上传学生证")
        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None or not user.is_active:
                raise NotFoundException("用户")
            existing = (
                await db.execute(
                    select(UserIdentity).where(UserIdentity.user_id == user_id)
                )
            ).scalar_one_or_none()
            if existing is not None:
                if existing.edit_count >= MAX_IDENTITY_EDITS:
                    raise BusinessException("实名信息最多修改 3 次，已达上限")
                existing.user_type = data.user_type
                existing.real_name = data.real_name
                existing.id_card_number = data.id_card_number
                existing.id_card_front_oss = data.id_card_front_oss
                existing.id_card_back_oss = data.id_card_back_oss
                existing.student_card_oss = data.student_card_oss
                existing.edit_count += 1
                await db.commit()
                await db.refresh(existing)
                return _mask_identity(existing)
            identity = UserIdentity(user_id=user_id, **data.model_dump())
            db.add(identity)
            await db.commit()
            await db.refresh(identity)
            return _mask_identity(identity)

    async def get_identity(self, user_id: int) -> UserIdentityResponse:
        async with get_db_ctx() as db:
            identity = (
                await db.execute(
                    select(UserIdentity).where(UserIdentity.user_id == user_id)
                )
            ).scalar_one_or_none()
            if identity is None:
                raise NotFoundException("实名认证信息")
            return _mask_identity(identity)

    async def decrypt_phone(self, user_id: int, encrypted_data: str, iv: str) -> str:
        session_key = await redis_client.get(f"{SESSION_KEY_PREFIX}{user_id}")
        if not session_key:
            raise BusinessException("session_key 已过期，请重新登录")
        phone = WechatClient.decrypt_phone(encrypted_data, iv, session_key)
        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None:
                raise NotFoundException("用户")
            user.phone = phone
            await db.commit()
        return phone
