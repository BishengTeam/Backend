from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import UnauthorizedException
from app.core.security import decode_access_token
from app.models.user import User


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
    return user
