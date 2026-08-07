import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.adapter.logging import client_ip_var, request_id_var


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        client_ip = request.client.host if request.client is not None else None
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
