from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import BusinessException, UnauthorizedException
from app.core.security import decode_access_token
from app.models.user import User
from app.models.user_identity import UserIdentity


async def get_current_user(
    authorization: str = Header(...),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not authorization.startswith("Bearer "):
        raise UnauthorizedException("认证格式错误")
    token = authorization[7:]
    try:
        payload = decode_access_token(token)
    except Exception:
        raise UnauthorizedException("登录已过期，请重新登录")
    user_id = payload.get("user_id")
    if user_id is None:
        raise UnauthorizedException("登录已过期，请重新登录")
    user = await db.get(User, user_id)
    if user is None:
        raise UnauthorizedException("用户不存在")
    if not user.is_active:
        raise UnauthorizedException("账号已注销")
    return user


async def require_identity(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    user_id = current_user.id
    identity = (
        await db.execute(
            select(UserIdentity).where(
                UserIdentity.user_id == user_id,
                UserIdentity.status == "verified",
            )
        )
    ).scalar_one_or_none()
    if identity is None:
        raise BusinessException("请先完成实名认证")
    return current_user
