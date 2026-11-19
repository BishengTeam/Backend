import logging

from sqlalchemy import select

from app.port.config import settings
from app.adapter.database import get_db_ctx
from app.port.exceptions import UnauthorizedException
from app.adapter.redis import redis_client, redis_get_safe, redis_setex_safe, redis_getdel_safe
from app.adapter.security import create_access_token, create_refresh_token, decode_access_token
from app.integrations.wechat import WechatClient
from app.domain.user.src.index import DeletedOpenid, User
from app.domain.user.src.index import UserProfile as DomainUserProfile
from app.schemas.user import LoginResponse, RefreshResponse, UserProfile
from app.services.renshe_audit import record_best_effort_audit

REFRESH_TOKEN_PREFIX = "refresh_token:"
REFRESH_TOKEN_TTL = 30 * 24 * 3600
SESSION_KEY_PREFIX = "session_key:"
SESSION_KEY_TTL = 7 * 24 * 3600

logger = logging.getLogger(__name__)


class AuthService:
    def __init__(self):
        self.wechat = WechatClient()

    async def login(self, code: str, phone_code: str | None = None) -> LoginResponse:
        try:
            return await self._login(code, phone_code)
        except Exception as exc:
            # Failed exchanges are useful for abuse diagnostics, but the
            # failed code/openid/error text is never persisted.
            await record_best_effort_audit(
                actor_type="user",
                actor_id=None,
                action="auth.login",
                object_type="auth",
                object_id=0,
                result="failed",
                summary={"error_type": type(exc).__name__},
            )
            raise

    async def _login(self, code: str, phone_code: str | None = None) -> LoginResponse:
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
                db.add(DomainUserProfile(user_id=user.id))
                # 2026-10-24 起新用户注册不再赠送积分（积分规则 v2，见 points_rules.py）。
            elif not user.is_active:
                raise UnauthorizedException("账号已注销，如需恢复请联系客服")
            else:
                # 存量用户可能缺 UserProfile，补建空记录
                existing = (
                    await db.execute(
                        select(DomainUserProfile).where(DomainUserProfile.user_id == user.id)
                    )
                ).scalar_one_or_none()
                if existing is None:
                    db.add(DomainUserProfile(user_id=user.id))
            # 登录按钮顺带微信手机号授权：仅在用户尚无手机号且不冲突时绑定
            if phone_code and not user.phone:
                await self._bind_phone_best_effort(db, user, phone_code)
            await db.commit()
            access_token = create_access_token(user.id, user.openid)
            refresh_token = create_refresh_token()
            await redis_setex_safe(
                f"{REFRESH_TOKEN_PREFIX}{refresh_token}", REFRESH_TOKEN_TTL, str(user.id)
            )
            if session_key:
                await redis_setex_safe(
                    f"{SESSION_KEY_PREFIX}{user.id}", SESSION_KEY_TTL, session_key
                )
            response = LoginResponse(
                access_token=access_token,
                refresh_token=refresh_token,
                expires_in=settings.JWT_EXPIRE_MINUTES * 60,
                user=UserProfile.model_validate(user),
            )
        await record_best_effort_audit(
            actor_type="user",
            actor_id=response.user.id,
            action="auth.login",
            object_type="user",
            object_id=response.user.id,
            result="succeeded",
            summary={"provider": "wechat"},
        )
        return response

    async def _bind_phone_best_effort(self, db, user, phone_code: str) -> None:
        """登录时顺带用微信手机号授权 code 绑定手机号。

        绑定失败（微信接口异常、号码冲突等）只记日志，绝不阻断登录。
        """
        try:
            phone = await self.wechat.get_phone_number(phone_code)
        except Exception as exc:  # noqa: BLE001 — 尽力而为，不影响登录
            logger.warning("登录时获取微信手机号失败: %s", exc)
            return
        if not phone:
            return
        conflict = (
            await db.execute(
                select(User).where(
                    User.phone == phone,
                    User.id != user.id,
                    User.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if conflict is not None:
            logger.warning("登录时手机号已被其他账号绑定，跳过: user_id=%s", user.id)
            return
        user.phone = phone

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
