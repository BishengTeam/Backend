import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt

from app.port.config import settings
from app.adapter.redis import (
    RedisUnavailableError,
    redis_get_required,
    redis_get_safe,
    redis_setex_safe,
)


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


ADMIN_REAUTH_TTL_SECONDS = 600
ADMIN_SESSION_EXPIRE_MINUTES = min(settings.JWT_EXPIRE_MINUTES, 120)


def create_admin_access_token(
    admin_id: int,
    username: str,
    role: str,
    *,
    auth_version: int = 1,
    session_mode: str = "normal",
    jti: str | None = None,
) -> str:
    """Create an administrator session token.

    ``auth_version`` is checked against the database on every authenticated
    request, which makes a password reset or account state change invalidate
    all existing sessions without enumerating JWTs.  ``jti`` also gives each
    browser/device session a stable identity for short-lived reauthentication.
    """

    if session_mode not in {"normal", "restricted"}:
        raise ValueError("invalid admin session mode")
    payload = {
        "type": "admin",
        "admin_id": admin_id,
        "username": username,
        "role": role,
        "jti": jti or str(uuid.uuid4()),
        "auth_version": auth_version,
        "session_mode": session_mode,
        "exp": datetime.now(timezone.utc)
        + timedelta(minutes=ADMIN_SESSION_EXPIRE_MINUTES),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])


def _jwt_blacklist_key(token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"jwt:blacklist:sha256:{digest}"


async def revoke_token(token: str) -> bool:
    """Add a token to Redis blacklist for its remaining validity.

    The caller receives ``False`` when revocation cannot be persisted, so a
    logout endpoint cannot claim success while the token remains usable.
    """
    try:
        payload = decode_access_token(token)
    except Exception:
        return False
    exp = payload.get("exp")
    if exp is None:
        return False
    now = datetime.now(timezone.utc).timestamp()
    ttl = int(exp - now)
    if ttl <= 0:
        return False
    return await redis_setex_safe(_jwt_blacklist_key(token), ttl, "1")


async def is_token_revoked(token: str) -> bool:
    """Check whether a token has been revoked.

    User sessions retain the historical cache-style fail-open behavior.
    Administrator sessions fail closed because Redis is authoritative for
    single-session logout; otherwise a revoked administrator token could
    become usable again during a Redis outage.
    """

    try:
        fail_closed = decode_access_token(token).get("type") == "admin"
    except Exception:
        # Invalid JWTs are rejected by the caller's decode step.  Keep this
        # helper free of a second, conflicting authentication error.
        fail_closed = False
    reader = redis_get_required if fail_closed else redis_get_safe
    try:
        result = await reader(_jwt_blacklist_key(token))
    except RedisUnavailableError:
        return True
    if result is not None:
        return True
    # Transitional read compatibility for tokens revoked before blacklist
    # keys were changed to one-way digests.  New writes never expose a JWT in
    # the Redis key space.
    try:
        legacy_result = await reader(f"jwt:blacklist:{token}")
    except RedisUnavailableError:
        return True
    return legacy_result is not None


def _admin_reauth_key(token: str) -> str:
    # Never place the bearer-like reauthentication credential itself in a
    # Redis key or log line.  A one-way digest is enough for exact lookup.
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"admin:reauth:{digest}"


async def create_admin_reauth_token(
    *, admin_id: int, jti: str, auth_version: int
) -> str | None:
    """Create a server-side, reusable ten-minute reauthentication grant.

    Redis is authoritative for this credential.  Unlike ordinary cache
    lookups, inability to persist the grant fails closed and returns ``None``.
    """

    token = secrets.token_urlsafe(48)
    payload = json.dumps(
        {
            "admin_id": admin_id,
            "jti": jti,
            "auth_version": auth_version,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    stored = await redis_setex_safe(
        _admin_reauth_key(token), ADMIN_REAUTH_TTL_SECONDS, payload
    )
    return token if stored else None


async def validate_admin_reauth_token(
    token: str | None,
    *,
    admin_id: int,
    jti: str,
    auth_version: int,
) -> bool:
    if not token:
        return False
    raw = await redis_get_safe(_admin_reauth_key(token))
    if raw is None:
        return False
    try:
        payload = json.loads(raw)
        return (
            int(payload.get("admin_id")) == admin_id
            and secrets.compare_digest(str(payload.get("jti", "")), jti)
            and int(payload.get("auth_version")) == auth_version
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
