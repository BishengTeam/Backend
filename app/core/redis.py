import redis.asyncio as aioredis
import redis.exceptions
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import settings

redis_client = aioredis.from_url(
    settings.REDIS_URL,
    encoding="utf-8",
    decode_responses=True,
    max_connections=20,
    socket_keepalive=True,
    retry_on_timeout=True,
    health_check_interval=30,
    socket_connect_timeout=5,
    socket_timeout=5,
)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=3),
    retry=retry_if_exception_type(redis.exceptions.ConnectionError),
)
async def redis_getdel_with_retry(key: str) -> str | None:
    """redis_client.getdel() with retry on ConnectionError."""
    return await redis_client.getdel(key)
