import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import router as api_router
from app.core.config import settings
from app.core.logging import setup_logging
from app.middleware import setup_middleware
from app.schemas.common import success
from app.services.cleanup import cleanup_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    task = asyncio.create_task(cleanup_loop())
    yield
    task.cancel()


app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    lifespan=lifespan,
)

setup_middleware(app)
app.include_router(api_router)


@app.get("/health")
async def health():
    return success()
