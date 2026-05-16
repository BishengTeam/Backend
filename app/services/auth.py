from sqlalchemy import select

from app.core.config import settings
from app.core.database import get_db_ctx
from app.core.exceptions import UnauthorizedException
from app.core.redis import redis_client
from app.core.security import create_access_token, create_refresh_token, decode_access_token
from app.integrations.wechat import WechatClient
from app.models.user import User
from app.schemas.user import LoginResponse, RefreshResponse, UserProfile

REFRESH_TOKEN_PREFIX = "refresh_token:"
REFRESH_TOKEN_TTL = 30 * 24 * 3600


class AuthService:
    def __init__(self):
        self.wechat = WechatClient()

    async def login(self, code: str) -> LoginResponse:
        wx_data = await self.wechat.code2session(code)
        openid = wx_data["openid"]
        async with get_db_ctx() as db:
            user = (await db.execute(select(User).where(User.openid == openid))).scalar_one_or_none()
            if user is None:
                user = User(openid=openid)
                db.add(user)
                await db.flush()
            elif not user.is_active:
                raise UnauthorizedException("账号已注销")
            access_token = create_access_token(user.id, user.openid)
            refresh_token = create_refresh_token()
            await redis_client.setex(
                f"{REFRESH_TOKEN_PREFIX}{user.id}", REFRESH_TOKEN_TTL, refresh_token
            )
            return LoginResponse(
                access_token=access_token,
                refresh_token=refresh_token,
                expires_in=settings.JWT_EXPIRE_MINUTES * 60,
                user=UserProfile.model_validate(user),
                poster_url=settings.LOGIN_POSTER_URL,
            )

    async def refresh(self, refresh_token: str) -> RefreshResponse:
        async with get_db_ctx() as db:
            for key_pattern in await redis_client.keys(f"{REFRESH_TOKEN_PREFIX}*"):
                stored_token = await redis_client.get(key_pattern)
                if stored_token == refresh_token:
                    user_id = int(key_pattern[len(REFRESH_TOKEN_PREFIX):])
                    user = await db.get(User, user_id)
                    if user is None or not user.is_active:
                        raise UnauthorizedException("账号不存在或已注销")
                    new_access_token = create_access_token(user.id, user.openid)
                    new_refresh_token = create_refresh_token()
                    await redis_client.setex(
                        f"{REFRESH_TOKEN_PREFIX}{user.id}", REFRESH_TOKEN_TTL, new_refresh_token
                    )
                    return RefreshResponse(
                        access_token=new_access_token,
                        refresh_token=new_refresh_token,
                        expires_in=settings.JWT_EXPIRE_MINUTES * 60,
                    )
        raise UnauthorizedException("refresh_token 无效或已过期")
