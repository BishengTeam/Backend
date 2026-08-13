"""Shared request rate limiting configuration.

The application has a small number of public/IP based limits (for example
login).  Quiz write/read limits are deliberately attached to the route with
``quiz_user_key`` so one busy user cannot throttle every user behind the same
NAT address.  The storage is Redis in normal deployments and an in-process
fallback is enabled only while Redis is unavailable; this keeps local/test
processes usable without silently turning the configured production path into
an IP bucket.
"""

from __future__ import annotations

from fastapi import Request
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from limits.storage import MemoryStorage

from app.port.config import settings


def _authenticated_user_key(request: Request, namespace: str) -> str:
    """Return a stable per-user key before FastAPI dependencies execute.

    slowapi checks a decorator before FastAPI dependencies are executed, so we
    cannot rely on ``get_current_user`` having populated request state yet.
    Decode the already supplied access token locally and fall back to a
    per-client anonymous key for invalid/missing credentials.  Authentication
    still runs in the normal dependency chain and remains authoritative.
    """

    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        if token:
            try:
                # Import lazily to avoid the security -> redis -> settings
                # import chain being evaluated while this module is loading.
                from app.adapter.security import decode_access_token

                payload = decode_access_token(token)
                user_id = payload.get("user_id")
                if payload.get("type") == "access":
                    # PyJWT preserves integer claims, while a few older
                    # clients emitted the same claim as a decimal string.
                    # Canonicalise both forms to one user bucket, but reject
                    # booleans, zero and non-integral values.
                    if isinstance(user_id, bool):
                        user_id = None
                    elif isinstance(user_id, str) and user_id.isdigit():
                        user_id = int(user_id)
                    if isinstance(user_id, int) and user_id > 0:
                        return f"{namespace}:user:{user_id}"
            except Exception:
                # Invalid tokens are rejected by auth; do not leak token data
                # into logs or rate-limit keys.
                pass
    return f"{namespace}:anonymous:{get_remote_address(request)}"


def quiz_user_key(request: Request) -> str:
    """Use a user bucket for quiz read/write limits."""

    return _authenticated_user_key(request, "quiz")


def quiz_admin_key(request: Request) -> str:
    """Use the authenticated administrator ID for management quotas."""

    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        if token:
            try:
                from app.adapter.security import decode_access_token

                payload = decode_access_token(token)
                admin_id = payload.get("admin_id")
                if isinstance(admin_id, bool):
                    admin_id = None
                elif isinstance(admin_id, str) and admin_id.isdigit():
                    admin_id = int(admin_id)
                if payload.get("type") == "admin" and isinstance(admin_id, int) and admin_id > 0:
                    return f"quiz:admin:{admin_id}"
            except Exception:
                pass
    return f"quiz:admin:anonymous:{get_remote_address(request)}"


def payment_user_key(request: Request) -> str:
    """Use a user bucket for active payment synchronization limits."""

    return _authenticated_user_key(request, "payment")


class ResilientLimiter(Limiter):
    """Use a local safety bucket when the distributed store is unavailable.

    slowapi's built-in fallback only accepts one static limit and therefore
    cannot preserve the route-specific 60/120 quotas.  This small adapter
    swaps the underlying strategy to ``MemoryStorage`` after a backend error,
    retaining every registered route limit.  A process restart restores the
    configured Redis backend; production readiness still requires Redis.
    """

    _using_local_fallback = False

    def _switch_to_local(self) -> None:
        local_storage = MemoryStorage()
        strategy_type = type(self._limiter)
        self._storage = local_storage
        self._limiter = strategy_type(local_storage)
        self._fallback_limiter = None
        self._storage_dead = False
        self._using_local_fallback = True

    def _check_request_limit(self, request, endpoint_func, in_middleware=True):
        try:
            return super()._check_request_limit(request, endpoint_func, in_middleware)
        except RateLimitExceeded:
            raise
        except Exception:
            if settings.APP_ENV in {"development", "test"} and not self._using_local_fallback:
                self._switch_to_local()
                return super()._check_request_limit(
                    request, endpoint_func, in_middleware
                )
            raise

    def reset(self) -> None:
        try:
            super().reset()
        except Exception:
            if settings.APP_ENV not in {"development", "test"}:
                raise
            self._switch_to_local()
            self._storage.reset()


# ``storage_uri`` is explicit so deployment configuration is not accidentally
# ignored by slowapi's optional ``.env`` discovery.  Production uses Redis;
# local/test runs use memory so a missing developer Redis cannot stall tests.
limiter = ResilientLimiter(
    key_func=get_remote_address,
    # Unit/integration runs deliberately use memory storage so a missing
    # developer Redis does not stall every request.  Staging/production use
    # the configured Redis backend and therefore share quotas across workers.
    storage_uri=(
        "memory://"
        if settings.APP_ENV in {"development", "test"}
        else settings.REDIS_URL
    ),
    in_memory_fallback_enabled=False,
    # FastAPI endpoints return Pydantic values rather than Starlette Response
    # objects; slowapi 0.1.x cannot inject headers into that shape reliably.
    # The contract's Retry-After header is added by our exception handler.
    headers_enabled=False,
)


__all__ = ["limiter", "payment_user_key", "quiz_admin_key", "quiz_user_key"]
