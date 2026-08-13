from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.contracts.quiz import DELETED_QUIZ_ENDPOINTS


def _matches_path(path: str, template: str) -> bool:
    """Match a removed route template without registering it in OpenAPI."""

    path_parts = path.strip("/").split("/")
    template_parts = template.strip("/").split("/")
    if len(path_parts) != len(template_parts):
        return False
    return all(
        template_part == path_part
        or (template_part.startswith("{") and template_part.endswith("}"))
        for path_part, template_part in zip(path_parts, template_parts, strict=True)
    )


class RemovedQuizRouteMiddleware(BaseHTTPMiddleware):
    """Return a stable 404 for explicitly removed quiz endpoint contracts."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        method = request.method.upper()
        path = request.url.path
        if any(
            method == removed_method and _matches_path(path, template)
            for removed_method, template in DELETED_QUIZ_ENDPOINTS
        ):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        return await call_next(request)
