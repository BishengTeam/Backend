import logging

import redis.asyncio as aioredis
import redis.exceptions
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.port.config import settings

logger = logging.getLogger(__name__)

# 主客户端：连接/读写超时均缩短至 1s，避免 Redis 不可用时阻塞整个请求链路
redis_client = aioredis.from_url(
    settings.REDIS_URL,
    encoding="utf-8",
    decode_responses=True,
    max_connections=20,
    socket_keepalive=True,
    retry_on_timeout=False,
    health_check_interval=30,
    socket_connect_timeout=1,
    socket_timeout=1,
)

# 全局状态标记：首次连接失败后置为 False，避免后续请求反复等待
_redis_available = True


async def redis_ping() -> bool:
    """尝试 ping Redis，更新可用性标记。"""
    global _redis_available
    try:
        await redis_client.ping()
        _redis_available = True
        return True
    except Exception:
        _redis_available = False
        return False


async def redis_get_safe(key: str) -> str | None:
    """Redis GET with fast-fail: 如果已知不可用，直接返回 None。"""
    global _redis_available
    if not _redis_available:
        return None
    try:
        return await redis_client.get(key)
    except Exception:
        # Cache keys can contain refresh tokens or JWTs.  Log only the
        # operation so diagnostics never disclose credential material.
        logger.warning("Redis GET failed; marking unavailable")
        _redis_available = False
        return None


async def redis_setex_safe(key: str, ttl: int, value: str) -> bool:
    """Redis SETEX with fast-fail."""
    global _redis_available
    if not _redis_available:
        return False
    try:
        await redis_client.setex(key, ttl, value)
        return True
    except Exception:
        logger.warning("Redis SETEX failed; marking unavailable")
        _redis_available = False
        return False


async def redis_getdel_safe(key: str) -> str | None:
    """Redis GETDEL with fast-fail (used by token refresh)."""
    global _redis_available
    if not _redis_available:
        return None
    try:
        return await redis_client.getdel(key)
    except Exception:
        logger.warning("Redis GETDEL failed; marking unavailable")
        _redis_available = False
        return None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=3),
    retry=retry_if_exception_type(redis.exceptions.ConnectionError),
)
async def redis_getdel_with_retry(key: str) -> str | None:
    """redis_client.getdel() with retry on ConnectionError."""
    return await redis_client.getdel(key)
