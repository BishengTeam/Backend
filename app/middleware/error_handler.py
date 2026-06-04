import logging
import re
import traceback

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.port.config import settings
from app.port.exceptions import AppException
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
        if not _SQL_PATTERN.search(line):
            lines.append(line)
    return lines


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    content = {"code": exc.code, "message": exc.message, "data": None}
    if hasattr(exc, "detail") and exc.detail:
        content["detail"] = exc.detail
    return JSONResponse(status_code=exc.http_status_code, content=content)


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
    logger.exception(
        "Unhandled exception on %s %s",
        request.method,
        request.url.path,
    )
    if settings.APP_DEBUG:
        tb_text = traceback.format_exc()
        exc_msg = str(exc)
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
