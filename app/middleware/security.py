import re

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.exceptions import AppException

SQL_INJECTION_PATTERN = re.compile(
    r"(?:union\s+select|select\s+.*\s+from|insert\s+into|drop\s+table|delete\s+from|"
    r"update\s+.*\s+set|exec\s*\(|execute\s*\(|--|\bOR\b.*=.*=)",
    re.IGNORECASE,
)
XSS_PATTERN = re.compile(r"<(?:script|iframe|embed|object|img\s+.*onerror)", re.IGNORECASE)

SENSITIVE_HEADERS = {"authorization", "cookie", "x-auth-token"}


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        url = str(request.url)
        query_string = str(request.query_params)
        for content in (url, query_string):
            if SQL_INJECTION_PATTERN.search(content):
                raise AppException(code=400, message="请求包含非法字符")
            if XSS_PATTERN.search(content):
                raise AppException(code=400, message="请求包含非法字符")
        response = await call_next(request)
        for header in SENSITIVE_HEADERS:
            if header in response.headers:
                del response.headers[header]
        return response
