import asyncio
import hashlib
import hmac
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, Response
from fastapi.openapi.utils import get_openapi
from fastapi.responses import PlainTextResponse
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import text

from app.api import router as api_router
from app.api.admin import router as admin_router
from app.api.agreement import router as agreement_router
from app.contracts.quiz import QUIZ_CONTRACT_VERSION
from app.port.config import settings
from app.adapter.database import engine, get_db_ctx
from app.adapter.redis import redis_client, redis_ping
from app.adapter.logging import setup_logging
from app.middleware import setup_middleware
from app.middleware.rate_limit import limiter
from app.schemas.common import success
from app.services.cleanup import cleanup_loop
from app.services.renshe_export import renshe_export_worker_loop
from app.services.renshe_cleanup import renshe_cleanup_worker_loop
from app.services.payment_reconciliation import (
    payment_reconciliation_metrics,
    payment_reconciliation_worker_loop,
)
from app.services.renshe_refund_reconciliation import (
    renshe_refund_reconciliation_metrics,
    renshe_refund_reconciliation_worker_loop,
)
from app.services.quiz_tasks import (
    enrich_quiz_task_snapshot,
    ensure_quiz_runtime_ready,
    quiz_task_snapshot_ready,
    quiz_worker_loop,
    read_quiz_task_snapshot,
)
from app.services.dependency_health import (
    enrich_oss_probe,
    enrich_quiz_oss_probe,
    inspect_oss_configuration,
    inspect_quiz_oss_configuration,
    inspect_wechat_login_configuration,
    inspect_wechat_payment_configuration,
    is_ready,
)
from app.services.quiz_metrics import QuizMetricsMiddleware, quiz_metrics

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    quiz_embedded_enabled = (
        settings.QUIZ_TASKS_ENABLED and settings.QUIZ_EMBEDDED_WORKER_ENABLED
    )
    if quiz_embedded_enabled:
        await ensure_quiz_runtime_ready()
    cleanup_task = asyncio.create_task(cleanup_loop())
    renshe_export_task = asyncio.create_task(renshe_export_worker_loop())
    renshe_cleanup_task = asyncio.create_task(renshe_cleanup_worker_loop())
    quiz_task = asyncio.create_task(quiz_worker_loop()) if quiz_embedded_enabled else None
    payment_reconciliation_task = (
        asyncio.create_task(payment_reconciliation_worker_loop())
        if settings.WECHAT_PAY_ENABLED
        else None
    )
    refund_reconciliation_task = (
        asyncio.create_task(renshe_refund_reconciliation_worker_loop())
        if settings.WECHAT_PAY_ENABLED
        else None
    )
    logger.info("Application startup complete")
    yield
    logger.info("Shutting down background tasks...")
    cleanup_task.cancel()
    renshe_export_task.cancel()
    renshe_cleanup_task.cancel()
    if quiz_task is not None:
        quiz_task.cancel()
    if payment_reconciliation_task is not None:
        payment_reconciliation_task.cancel()
    if refund_reconciliation_task is not None:
        refund_reconciliation_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    try:
        await renshe_export_task
    except asyncio.CancelledError:
        pass
    try:
        await renshe_cleanup_task
    except asyncio.CancelledError:
        pass
    if quiz_task is not None:
        try:
            await quiz_task
        except asyncio.CancelledError:
            pass
    if payment_reconciliation_task is not None:
        try:
            await payment_reconciliation_task
        except asyncio.CancelledError:
            pass
    if refund_reconciliation_task is not None:
        try:
            await refund_reconciliation_task
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


RENSHE_CONTRACT_VERSION = "2026-08-10"
RENSHE_ERROR_CODES = {
    "40001": {"status": 422, "description": "参数或材料校验失败"},
    "40100": {"status": 401, "description": "未登录或 Token 无效"},
    "40101": {"status": 403, "description": "无权限或资源越权"},
    "40200": {"status": 422, "description": "业务规则不允许"},
    "40201": {"status": 409, "description": "当前状态冲突"},
    "40300": {"status": 404, "description": "资源不存在"},
    "40400": {"status": 502, "description": "第三方服务错误"},
    "50000": {"status": 500, "description": "服务内部错误"},
}
RENSHE_ADDITIONAL_CONTRACT_PATHS = {
    "/admin/certifications/{code}/plans/{plan_id}/impact",
    "/api/payment/prepay",
    "/api/payment/orders/{order_id}/sync",
    "/api/payment/callback",
    "/api/payment/refund-callback",
}
RENSHE_PUBLIC_CALLBACK_PATHS = {
    "/api/payment/callback",
    "/api/payment/refund-callback",
}

QUIZ_ERROR_CODES = {
    "40001": {"status": 422, "description": "参数校验失败"},
    "40100": {"status": 401, "description": "请先登录"},
    "40101": {"status": 403, "description": "无权限"},
    "40200": {"status": 422, "description": "业务规则不允许"},
    "40201": {"status": 409, "description": "版本或状态冲突"},
    "40202": {"status": 429, "description": "请求过于频繁"},
    "40300": {"status": 404, "description": "资源不存在"},
    "50000": {"status": 500, "description": "服务内部错误"},
}

QUIZ_RATE_LIMITS = {
    ("get", "/api/quiz/questions"): 60,
    ("post", "/api/quiz/practice-sessions/{session_id}/attempts"): 120,
    ("put", "/api/quiz/exams/{exam_id}/answers/{exam_question_id}"): 120,
    ("post", "/admin/quiz/categories"): 120,
    ("put", "/admin/quiz/categories/{category_id}"): 120,
    ("delete", "/admin/quiz/categories/{category_id}"): 120,
    ("post", "/admin/quiz/categories/{category_id}/status"): 120,
    ("get", "/admin/quiz/categories/{category_id}/impact"): 120,
    ("post", "/admin/quiz/questions"): 120,
    ("put", "/admin/quiz/questions/{question_id}"): 120,
    ("delete", "/admin/quiz/questions/{question_id}"): 120,
    ("post", "/admin/quiz/questions/{question_id}/publish"): 120,
    ("post", "/admin/quiz/questions/{question_id}/disable"): 120,
    ("post", "/admin/quiz/questions/{question_id}/restore"): 120,
    ("post", "/admin/quiz/questions/batch-publish"): 30,
    ("post", "/admin/quiz/questions/batch-disable"): 30,
    ("post", "/admin/quiz/imports/csv"): 10,
    ("post", "/admin/quiz/imports/json"): 10,
    ("post", "/admin/quiz/imports/{job_id}/retry"): 10,
    ("post", "/admin/quiz/imports/{job_id}/confirm-categories"): 10,
    ("post", "/admin/quiz/imports/{job_id}/cancel"): 10,
    ("get", "/admin/quiz/imports/{job_id}/source-url"): 60,
    ("get", "/admin/quiz/imports/{job_id}/report-url"): 60,
}


def _add_renshe_contract_metadata(schema: dict) -> None:
    """Add machine-readable error codes to the frozen human-resources API."""

    error_schema = {
        "type": "object",
        "properties": {
            "code": {"type": "integer", "example": 40200},
            "message": {"type": "string", "example": "当前状态不允许此操作"},
            "data": {"type": ["object", "null"]},
            "detail": {
                "type": ["array", "object", "null"],
                "description": "可选的字段级校验详情；不包含敏感原文",
            },
        },
        "required": ["code", "message"],
    }
    schema.setdefault("components", {}).setdefault("schemas", {})[
        "APIErrorResponse"
    ] = error_schema
    for path, path_item in schema.get("paths", {}).items():
        if (
            "/api/renshe" not in path
            and "/admin/renshe" not in path
            and path not in RENSHE_ADDITIONAL_CONTRACT_PATHS
        ):
            continue
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            operation["x-contract-version"] = RENSHE_CONTRACT_VERSION
            if path in RENSHE_PUBLIC_CALLBACK_PATHS:
                operation["x-wechat-pay-api-version"] = "v3"
                operation["x-notification-ack"] = {
                    "success": {"code": "SUCCESS", "message": "成功"},
                    "failure": {"code": "FAIL", "message": "失败"},
                }
                continue
            operation["x-error-codes"] = sorted(RENSHE_ERROR_CODES)
            responses = operation.setdefault("responses", {})
            for code, metadata in RENSHE_ERROR_CODES.items():
                status = str(metadata["status"])
                responses.setdefault(
                    status,
                    {
                        "description": f"{metadata['description']}（业务码 {code}）",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/APIErrorResponse"},
                                "examples": {
                                    "error": {
                                        "value": {
                                            "code": int(code),
                                            "message": metadata["description"],
                                            "data": None,
                                        }
                                    }
                                },
                            }
                        },
                    },
                )


def _add_quiz_contract_metadata(schema: dict) -> None:
    """Publish the frozen quiz error/rate-limit contract in OpenAPI."""

    for path, path_item in schema.get("paths", {}).items():
        if not (path.startswith("/api/quiz") or path.startswith("/admin/quiz")):
            continue
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            operation["x-quiz-contract-version"] = QUIZ_CONTRACT_VERSION
            operation["x-error-codes"] = sorted(QUIZ_ERROR_CODES)
            limit = QUIZ_RATE_LIMITS.get((method, path))
            if limit is not None:
                operation["x-rate-limit-per-minute"] = limit
            responses = operation.setdefault("responses", {})
            for code, metadata in QUIZ_ERROR_CODES.items():
                status = str(metadata["status"])
                # Multiple business codes intentionally share HTTP 422; the
                # machine-readable x-error-codes list preserves the distinction.
                responses.setdefault(
                    status,
                    {
                        "description": (
                            f"{metadata['description']}（业务码 {code}）"
                        ),
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/APIErrorResponse"},
                                "examples": {
                                    "error": {
                                        "value": {
                                            "code": int(code),
                                            "message": metadata["description"],
                                            "data": None,
                                        }
                                    }
                                },
                            }
                        },
                    },
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
    _add_renshe_contract_metadata(schema)
    _add_quiz_contract_metadata(schema)
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi

setup_middleware(app)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)
# Added last so this pure-ASGI middleware wraps SlowAPI too and therefore
# counts throttled requests that never reach a route function.
app.add_middleware(QuizMetricsMiddleware)
app.include_router(api_router)
app.include_router(admin_router)
app.include_router(agreement_router)


@app.get(
    "/internal/metrics",
    include_in_schema=False,
    response_class=PlainTextResponse,
)
async def internal_metrics(
    authorization: str | None = Header(default=None, include_in_schema=False),
):
    """Prometheus scrape target protected by a dedicated deployment token."""

    if not settings.QUIZ_METRICS_ENABLED:
        return PlainTextResponse("Not Found\n", status_code=404)
    expected = settings.QUIZ_METRICS_BEARER_TOKEN
    supplied = ""
    if authorization and authorization.startswith("Bearer "):
        supplied = authorization[7:].strip()
    if not expected or not hmac.compare_digest(supplied, expected):
        return PlainTextResponse(
            "Unauthorized\n",
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    quiz_tasks = enrich_quiz_task_snapshot(await read_quiz_task_snapshot())
    quiz_metrics.update_worker(quiz_tasks)
    return PlainTextResponse(
        quiz_metrics.render(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.get(
    "/health",
    summary="健康检查",
    description=(
        "进程存活检查并诊断数据库、Redis、私有 OSS、微信登录和微信支付配置；"
        "不返回任何密钥。由 liveness probe 或负载均衡器使用。"
    ),
)
async def health():
    checks, details = await _dependency_checks(probe_external=False)
    quiz_tasks = enrich_quiz_task_snapshot(await read_quiz_task_snapshot())
    details["quiz_tasks"] = quiz_tasks
    checks["quiz_worker"] = "ok" if quiz_task_snapshot_ready(quiz_tasks) else "unavailable"
    details["payment_reconciliation"] = payment_reconciliation_metrics.snapshot()
    details["refund_reconciliation"] = (
        renshe_refund_reconciliation_metrics.snapshot()
    )
    all_ok = checks["database"] == "ok" and checks["redis"] == "ok"
    return {
        "code": 0 if all_ok else 50000,
        "message": "ok" if all_ok else "degraded",
        "data": {
            "status": "ok" if all_ok else "degraded",
            "checks": checks,
            "details": details,
        },
    }


@app.get(
    "/ready",
    summary="就绪检查",
    description=(
        "就绪探针。检查当前环境要求的数据库、Redis、OSS、微信登录和微信支付"
        "依赖；未就绪时返回 503，部署脚本不得切入流量。"
    ),
)
async def ready(response: Response):
    checks, details = await _dependency_checks(probe_external=True)
    quiz_tasks = enrich_quiz_task_snapshot(await read_quiz_task_snapshot())
    details["quiz_tasks"] = quiz_tasks
    checks["quiz_worker"] = "ok" if quiz_task_snapshot_ready(quiz_tasks) else "unavailable"
    details["payment_reconciliation"] = payment_reconciliation_metrics.snapshot()
    details["refund_reconciliation"] = (
        renshe_refund_reconciliation_metrics.snapshot()
    )
    ready_ok = is_ready(checks)
    response.status_code = 200 if ready_ok else 503
    return {
        "code": 0 if ready_ok else 50000,
        "status": "ready" if ready_ok else "not_ready",
        "checks": checks,
        "details": details,
    }


async def _dependency_checks(
    *, probe_external: bool
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """Collect dependency states while preserving the legacy check shape.

    ``checks`` intentionally keeps the old string values (database/Redis
    clients and monitoring scripts consume them).  ``details`` contains safe
    diagnostic metadata for the new GOV-08/BE-18 contract.
    """

    async def _bounded(check) -> Any:
        try:
            return await asyncio.wait_for(
                check(), timeout=settings.HEALTH_CHECK_TIMEOUT_SECONDS
            )
        except Exception:
            return False

    database_probe, redis_ok = await asyncio.gather(
        _bounded(_database_probe), _bounded(_check_redis)
    )
    if isinstance(database_probe, dict):
        database_details = database_probe
        db_ok = database_probe.get("status") == "ok"
    else:
        # Keep the readiness result fail-closed if a custom deployment probe
        # still returns only a boolean. Production's built-in probe also
        # exposes a non-reversible database fingerprint for UAT target binding.
        db_ok = bool(database_probe)
        database_details = {"status": "ok" if db_ok else "unavailable"}
    oss_details = inspect_oss_configuration()
    quiz_oss_details = inspect_quiz_oss_configuration()
    # Staging deployments can use a real private bucket too.  Probe every
    # network-backed OSS configuration; local development storage is handled
    # as an intentional no-network adapter by ``enrich_oss_probe``.
    if probe_external and oss_details.get("mode") == "aliyun_oss":
        oss_details = await enrich_oss_probe(oss_details)
    if probe_external and quiz_oss_details.get("status") == "ok":
        quiz_oss_details = await enrich_quiz_oss_probe(quiz_oss_details)
    login_details = inspect_wechat_login_configuration()
    payment_details = inspect_wechat_payment_configuration()
    details: dict[str, dict[str, Any]] = {
        "database": database_details,
        "redis": {"status": "ok" if redis_ok else "unavailable"},
        "oss": oss_details,
        "quiz_oss": quiz_oss_details,
        "wechat_login": login_details,
        "wechat_payment": payment_details,
    }
    checks = {
        "database": details["database"]["status"],
        "redis": details["redis"]["status"],
        "oss": oss_details["status"],
        "quiz_oss": quiz_oss_details["status"],
        "wechat_login": login_details["status"],
        "wechat_payment": payment_details["status"],
    }
    return checks, details


async def _database_probe() -> dict[str, Any]:
    """Return readiness plus a one-way database identity for safe UAT binding."""

    try:
        async with get_db_ctx() as db:
            identity = (
                await db.execute(
                    text(
                        "SELECT current_database(), "
                        "(SELECT system_identifier::text FROM pg_control_system())"
                    )
                )
            ).one()
        database_name = str(identity[0])
        fingerprint = hashlib.sha256(
            f"{identity[1]}/{database_name}".encode("utf-8")
        ).hexdigest()
        return {"status": "ok", "fingerprint_sha256": fingerprint}
    except Exception:
        return {"status": "unavailable"}


async def _check_redis() -> bool:
    return await redis_ping()
