from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

SENSITIVE_HEADERS = {"authorization", "cookie", "x-auth-token"}


class SecureHeadersMiddleware(BaseHTTPMiddleware):
    """Strip sensitive headers from responses."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        for header in SENSITIVE_HEADERS:
            if header in response.headers:
                del response.headers[header]
        return response
