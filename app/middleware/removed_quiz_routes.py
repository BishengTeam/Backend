from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.contracts.quiz import DELETED_QUIZ_ENDPOINTS
from app.middleware.route_match import matches_retired_endpoint


class RemovedQuizRouteMiddleware(BaseHTTPMiddleware):
    """Return a stable 404 for explicitly removed quiz endpoint contracts."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if matches_retired_endpoint(
            request.method,
            request.url.path,
            DELETED_QUIZ_ENDPOINTS,
        ):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        return await call_next(request)
