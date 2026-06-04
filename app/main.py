import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import text

from app.api import router as api_router
from app.api.admin import router as admin_router
from app.api.agreement import router as agreement_router
from app.core.config import settings
from app.core.database import engine, get_db_ctx
from app.core.redis import redis_client, redis_ping
from app.core.logging import setup_logging
from app.middleware import setup_middleware
from app.middleware.rate_limit import limiter
from app.schemas.common import success
from app.services.cleanup import cleanup_loop

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    cleanup_task = asyncio.create_task(cleanup_loop())
    logger.info("Application startup complete")
    yield
    logger.info("Shutting down background tasks...")
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    logger.info("Closing database connections...")
    await engine.dispose()
    logger.info("Closing Redis connections...")
    await redis_client.close()
    logger.info("Application shutdown complete")


app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    lifespan=lifespan,
)

setup_middleware(app)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)
app.include_router(api_router)
app.include_router(admin_router)
app.include_router(agreement_router)


@app.get("/health")
async def health():
    db_ok = await _check_db()
    redis_ok = await _check_redis()
    all_ok = db_ok and redis_ok
    return {
        "code": 0 if all_ok else 50000,
        "message": "ok" if all_ok else "degraded",
        "data": {
            "status": "ok" if all_ok else "degraded",
            "checks": {
                "database": "ok" if db_ok else "unavailable",
                "redis": "ok" if redis_ok else "unavailable",
            },
        },
    }


@app.get("/ready")
async def ready():
    return {"status": "ready"}


async def _check_db() -> bool:
    try:
        async with get_db_ctx() as db:
            await db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def _check_redis() -> bool:
    return await redis_ping()
