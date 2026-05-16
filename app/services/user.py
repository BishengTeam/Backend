from app.core.database import get_db_ctx
from app.core.exceptions import BusinessException, NotFoundException
from app.integrations.wechat import WechatClient
from app.models.user import User
from app.schemas.user import UserProfile, UserProfileUpdate

MAX_PROFILE_EDITS = 3


class UserService:

    async def get_profile(self, user_id: int) -> UserProfile:
        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None or not user.is_active:
                raise NotFoundException("用户")
            return UserProfile.model_validate(user)

    async def update_profile(self, user_id: int, data: UserProfileUpdate) -> UserProfile:
        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None or not user.is_active:
                raise NotFoundException("用户")
            if user.profile_edit_count >= MAX_PROFILE_EDITS:
                raise BusinessException("资料修改次数已达上限")
            update_data = data.model_dump(exclude_unset=True)
            for key, value in update_data.items():
                setattr(user, key, value)
            user.profile_edit_count += 1
            await db.commit()
            await db.refresh(user)
            return UserProfile.model_validate(user)

    async def delete_account(self, user_id: int) -> None:
        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None:
                raise NotFoundException("用户")
            user.is_active = False
            await db.commit()

    async def decrypt_phone(self, user_id: int, encrypted_data: str, iv: str, code: str) -> str:
        wechat = WechatClient()
        wx_data = await wechat.code2session(code)
        session_key = wx_data.get("session_key", "")
        phone = WechatClient.decrypt_phone(encrypted_data, iv, session_key)
        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None:
                raise NotFoundException("用户")
            user.phone = phone
            await db.commit()
        return phone
