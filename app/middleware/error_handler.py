from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.exceptions import AppException


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
    return JSONResponse(
        status_code=500,
        content={"code": 50000, "message": "服务器内部错误", "data": None},
    )
