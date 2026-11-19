import uuid
import re

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.adapter.logging import client_ip_var, request_id_var

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        incoming = (request.headers.get("X-Request-ID") or "").strip()
        # 只接受受控字符集与长度，避免审计列溢出与日志关联碰撞。
        request_id = incoming if _REQUEST_ID_PATTERN.fullmatch(incoming) else str(uuid.uuid4())
        request.state.request_id = request_id
        from app.middleware.client_ip import trusted_client_ip

        client_ip = trusted_client_ip(request)
        request.state.client_ip = client_ip
        request_token = request_id_var.set(request_id)
        ip_token = client_ip_var.set(client_ip)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            client_ip_var.reset(ip_token)
            request_id_var.reset(request_token)
