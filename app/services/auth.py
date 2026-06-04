from sqlalchemy import select

from app.port.config import settings
from app.adapter.database import get_db_ctx
from app.port.exceptions import UnauthorizedException
from app.adapter.redis import redis_client, redis_get_safe, redis_setex_safe, redis_getdel_safe
from app.adapter.security import create_access_token, create_refresh_token, decode_access_token
from app.integrations.wechat import WechatClient
from app.models.deleted_openid import DeletedOpenid
from app.models.user import User
from app.schemas.user import LoginResponse, RefreshResponse, UserProfile

REFRESH_TOKEN_PREFIX = "refresh_token:"
REFRESH_TOKEN_TTL = 30 * 24 * 3600
SESSION_KEY_PREFIX = "session_key:"
SESSION_KEY_TTL = 7 * 24 * 3600


class AuthService:
    def __init__(self):
        self.wechat = WechatClient()

    async def login(self, code: str) -> LoginResponse:
        wx_data = await self.wechat.code2session(code)
        openid = wx_data["openid"]
        session_key = wx_data.get("session_key", "")
        async with get_db_ctx() as db:
            deleted = (
                await db.execute(select(DeletedOpenid).where(DeletedOpenid.openid == openid))
            ).scalar_one_or_none()
            if deleted is not None:
                raise UnauthorizedException("该账号已注销，30 天内不可重新注册")
            user = (await db.execute(select(User).where(User.openid == openid))).scalar_one_or_none()
            if user is None:
                user = User(openid=openid)
                db.add(user)
                await db.flush()
            elif not user.is_active:
                raise UnauthorizedException("账号已注销，如需恢复请联系客服")
            access_token = create_access_token(user.id, user.openid)
            refresh_token = create_refresh_token()
            await redis_setex_safe(
                f"{REFRESH_TOKEN_PREFIX}{refresh_token}", REFRESH_TOKEN_TTL, str(user.id)
            )
            if session_key:
                await redis_setex_safe(
                    f"{SESSION_KEY_PREFIX}{user.id}", SESSION_KEY_TTL, session_key
                )
            return LoginResponse(
                access_token=access_token,
                refresh_token=refresh_token,
                expires_in=settings.JWT_EXPIRE_MINUTES * 60,
                user=UserProfile.model_validate(user),
            )

    async def refresh(self, refresh_token: str) -> RefreshResponse:
        key = f"{REFRESH_TOKEN_PREFIX}{refresh_token}"
        user_id_raw = await redis_getdel_safe(key)
        if user_id_raw is None:
            raise UnauthorizedException("refresh_token 无效或已过期")
        user_id = int(user_id_raw)
        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None or not user.is_active:
                raise UnauthorizedException("账号不存在或已注销")
            new_access_token = create_access_token(user.id, user.openid)
            new_refresh_token = create_refresh_token()
            await redis_setex_safe(
                f"{REFRESH_TOKEN_PREFIX}{new_refresh_token}",
                REFRESH_TOKEN_TTL,
                str(user.id),
            )
            return RefreshResponse(
                access_token=new_access_token,
                refresh_token=new_refresh_token,
                expires_in=settings.JWT_EXPIRE_MINUTES * 60,
            )

    @staticmethod
    async def logout(refresh_token: str) -> None:
        key = f"{REFRESH_TOKEN_PREFIX}{refresh_token}"
        # delete 不影响核心流程，Redis 不可用时静默跳过
        try:
            await redis_client.delete(key)
        except Exception:
            pass
