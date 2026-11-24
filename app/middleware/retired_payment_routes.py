"""Answer ``410 Gone`` for retired WeChat Pay endpoints.

The middleware runs before routing, authentication and any provider client so
a stale Admin or mini-program bundle cannot trigger a WeChat Pay call even if
it still holds a valid session.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.contracts.payment import (
    RETIRED_WECHAT_PAY_ENDPOINTS,
    WECHAT_PAY_RETIRED_CODE,
    WECHAT_PAY_RETIRED_MESSAGE,
)
from app.middleware.route_match import matches_retired_endpoint


class RetiredPaymentRouteMiddleware(BaseHTTPMiddleware):
    """Fail closed for the retired WeChat Pay HTTP contract."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if matches_retired_endpoint(
            request.method,
            request.url.path,
            RETIRED_WECHAT_PAY_ENDPOINTS,
        ):
            return JSONResponse(
                status_code=410,
                content={
                    "code": WECHAT_PAY_RETIRED_CODE,
                    "message": WECHAT_PAY_RETIRED_MESSAGE,
                    "data": None,
                },
            )
        return await call_next(request)


__all__ = ["RetiredPaymentRouteMiddleware"]
