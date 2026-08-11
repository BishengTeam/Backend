import logging

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapter.database import get_db
from app.port.exceptions import AppException, BusinessException, ForbiddenException, UnauthorizedException
from app.adapter.security import decode_access_token, is_token_revoked
from app.domain.user.src.index import AdminUser, User, UserRealname
from app.policy.permissions import ROLE_PERMISSIONS


logger = logging.getLogger(__name__)


async def get_current_user(
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise UnauthorizedException("认证格式错误")
    token = authorization[7:]
    try:
        payload = decode_access_token(token)
    except Exception as e:
        # Never include the bearer token (or an exception string that may echo
        # it) in application logs.  The request id middleware supplies the
        # correlation context when a deployment needs to trace the failure.
        logger.warning("JWT decode failed: exception_type=%s", type(e).__name__)
        raise UnauthorizedException("登录已过期，请重新登录")
    if await is_token_revoked(token):
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


async def get_current_admin(
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
):
    if not authorization or not authorization.startswith("Bearer "):
        raise UnauthorizedException("认证格式错误")
    token = authorization[7:]
    try:
        payload = decode_access_token(token)
    except Exception as e:
        logger.warning("admin JWT decode failed: exception_type=%s", type(e).__name__)
        raise UnauthorizedException("登录已过期，请重新登录")
    if await is_token_revoked(token):
        raise UnauthorizedException("登录已过期，请重新登录")
    if payload.get("type") != "admin":
        raise UnauthorizedException("请使用管理员账号登录")
    admin_id = payload.get("admin_id")
    if admin_id is None:
        raise UnauthorizedException("登录已过期，请重新登录")
    admin = await db.get(AdminUser, admin_id)
    if admin is None:
        raise UnauthorizedException("管理员不存在")
    if not admin.is_active:
        raise UnauthorizedException("账号已被禁用")
    return admin


async def get_current_user_optional(
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    if not authorization:
        return None
    try:
        return await get_current_user(authorization=authorization, db=db)
    except AppException:
        return None


async def require_identity(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    user_id = current_user.id
    identity = (
        await db.execute(
            select(UserRealname).where(
                UserRealname.user_id == user_id,
                UserRealname.status == "verified",
            )
        )
    ).scalar_one_or_none()
    if identity is None:
        raise BusinessException("请先完成实名认证")
    return current_user


def require_permission(permission: str):
    """Dependency factory: returns a dependency that checks admin permission."""

    async def _check(admin=Depends(get_current_admin)):
        if admin.role == "super_admin":
            return admin
        allowed = ROLE_PERMISSIONS.get(admin.role, [])
        if permission not in allowed and "*" not in allowed:
            raise ForbiddenException(f"缺少权限: {permission}")
        return admin

    return _check


async def require_super_admin(admin=Depends(get_current_admin)):
    if admin.role != "super_admin":
        raise ForbiddenException("仅超级管理员可执行此操作")
    return admin
