from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI

from app.port.config import settings


def setup_cors(app: FastAPI) -> None:
    if settings.APP_ENV == "development":
        allow_origins = [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://localhost:8080",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:5173",
            "http://127.0.0.1:8080",
        ]
    else:
        allow_origins = settings.CORS_ORIGINS

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )