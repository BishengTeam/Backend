from fastapi import FastAPI

from app.core.exceptions import AppException
from app.middleware.cors import setup_cors
from app.middleware.error_handler import app_exception_handler, global_exception_handler
from app.middleware.request_id import RequestIDMiddleware


def setup_middleware(app: FastAPI) -> None:
    setup_cors(app)
    app.add_middleware(RequestIDMiddleware)
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(Exception, global_exception_handler)
