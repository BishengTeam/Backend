import logging
import re
import traceback

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from app.port.config import settings
from app.port.exceptions import AppException
from app.utils.audit import redact_sensitive_text
from pathlib import Path

logger = logging.getLogger(__name__)
_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
_SQL_PATTERN = re.compile(
    r"\b(SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|FROM|WHERE|JOIN|INTO|VALUES|SET)\b",
    re.IGNORECASE,
)


def _sanitize_traceback(tb: str) -> list[str]:
    """Sanitize traceback by replacing project paths and stripping SQL fragments."""
    lines: list[str] = []
    for line in tb.split("\n"):
        line = line.replace(_PROJECT_ROOT, "<project_root>")
        line = redact_sensitive_text(line) or line
        if not _SQL_PATTERN.search(line):
            lines.append(line)
    return lines


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    content = {"code": exc.code, "message": exc.message, "data": None}
    if hasattr(exc, "detail") and exc.detail:
        content["detail"] = exc.detail
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        content["request_id"] = request_id
    return JSONResponse(status_code=exc.http_status_code, content=content)


async def rate_limit_exception_handler(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    """Expose the frozen quiz throttling contract for slowapi failures.

    slowapi raises ``RateLimitExceeded`` before the endpoint function is
    entered, therefore no service/database mutation can occur.  Its default
    handler returns ``{"error": ...}``, which is not part of the API contract;
    map it to the stable business code while retaining standard rate-limit
    headers when the middleware has populated them.
    """

    response = JSONResponse(
        status_code=429,
        content={
            "code": 40202,
            "message": "请求过于频繁，请稍后再试",
            "data": None,
        },
        headers={"Retry-After": "60"},
    )
    # The exception may be handled by the decorator (where app.state exists)
    # or by SlowAPIMiddleware.  Header injection is best-effort and must never
    # turn a throttled request into a 500 response.
    try:
        limiter = request.app.state.limiter
        current_limit = getattr(request.state, "view_rate_limit", None)
        if current_limit is not None:
            response = limiter._inject_headers(response, current_limit)
    except Exception:
        pass
    return response


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    detail = []
    for error in exc.errors():
        field = ".".join(
            str(loc) for loc in error["loc"] if loc not in ("body", "query", "path")
        )
        detail.append({"field": field, "reason": error["msg"]})
    return JSONResponse(
        status_code=422,
        content={"code": 40001, "message": "参数校验失败", "detail": detail},
    )


async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # ``logger.exception`` would serialize the raw exception/traceback, which
    # can contain request PII or database bind values.  The debug response is
    # sanitized below; operational logs retain only a safe type and route.
    logger.error(
        "Unhandled exception on %s %s exception_type=%s",
        request.method,
        request.url.path,
        type(exc).__name__,
    )
    if settings.APP_DEBUG:
        tb_text = traceback.format_exc()
        exc_msg = redact_sensitive_text(str(exc)) or type(exc).__name__
        if _SQL_PATTERN.search(exc_msg):
            exc_msg = "[SQL content redacted]"
        return JSONResponse(
            status_code=500,
            content={
                "code": 50000,
                "message": "服务器内部错误",
                "detail": {
                    "exception_type": type(exc).__name__,
                    "exception_message": exc_msg,
                    "traceback": _sanitize_traceback(tb_text),
                },
            },
        )
    return JSONResponse(
        status_code=500,
        content={"code": 50000, "message": "服务器内部错误", "data": None},
    )
