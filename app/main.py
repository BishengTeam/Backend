import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import text

from app.api import router as api_router
from app.api.admin import router as admin_router
from app.api.agreement import router as agreement_router
from app.port.config import settings
from app.adapter.database import engine, get_db_ctx
from app.adapter.redis import redis_client, redis_ping
from app.adapter.logging import setup_logging
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
    swagger_ui_parameters={"persistAuthorization": True},
)


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=settings.APP_NAME,
        version="0.1.0",
        routes=app.routes,
    )
    schema["components"]["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }
    }
    # 为有 Authorization header 的接口：移除参数区文本框，改用全局 security 方案
    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            params = operation.get("parameters", [])
            auth_params = [p for p in params if p.get("name", "").lower() == "authorization"]
            if auth_params:
                # 移除参数区的 authorization 文本框
                operation["parameters"] = [p for p in params if p not in auth_params]
                # 添加到全局 security 方案
                operation.setdefault("security", []).append({"BearerAuth": []})
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi

setup_middleware(app)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)
app.include_router(api_router)
app.include_router(admin_router)
app.include_router(agreement_router)


@app.get("/health", summary="健康检查", description="服务健康检查，验证数据库和 Redis 连接状态。由 K8s liveness probe 或负载均衡器掉线检测使用。")
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


@app.get("/ready", summary="就绪检查", description="服务就绪探针，检查服务是否可以接收流量。由 K8s readiness probe 使用，返回就绪状态。")
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
