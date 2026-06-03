import secrets
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import settings
from app.core.redis import redis_client


def create_access_token(user_id: int, openid: str) -> str:
    payload = {
        "type": "access",
        "user_id": user_id,
        "openid": openid,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def create_admin_access_token(admin_id: int, username: str, role: str) -> str:
    payload = {
        "type": "admin",
        "admin_id": admin_id,
        "username": username,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])


async def revoke_token(token: str) -> None:
    """Add token to Redis blacklist with TTL matching remaining validity."""
    try:
        payload = decode_access_token(token)
    except Exception:
        return  # invalid/expired token, no need to blacklist
    exp = payload.get("exp")
    if exp is None:
        return
    now = datetime.now(timezone.utc).timestamp()
    ttl = int(exp - now)
    if ttl <= 0:
        return
    await redis_client.setex(f"jwt:blacklist:{token}", ttl, "1")


async def is_token_revoked(token: str) -> bool:
    """Check whether a token has been revoked."""
    return await redis_client.get(f"jwt:blacklist:{token}") is not None
