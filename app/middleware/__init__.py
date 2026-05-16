from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.core.exceptions import AppException
from app.middleware.cors import setup_cors
from app.middleware.error_handler import (
    app_exception_handler,
    global_exception_handler,
    validation_exception_handler,
)
from app.middleware.request_id import RequestIDMiddleware
from app.middleware.security import SecurityMiddleware


def setup_middleware(app: FastAPI) -> None:
    setup_cors(app)
    app.add_middleware(SecurityMiddleware)
    app.add_middleware(RequestIDMiddleware)
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, global_exception_handler)
