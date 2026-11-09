from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.port.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.APP_DEBUG,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_pre_ping=True,
    pool_recycle=3600,
)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


from datetime import datetime

from sqlalchemy import DateTime, Integer, func
from sqlalchemy.orm import Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RecycleBinMixin:
    """软删除进回收站：删除时写 deleted_at 并给 30 自然日 restore_until，到期由清理任务永久删除。"""

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    restore_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


async def get_db() -> AsyncSession:
    async with async_session_factory() as session:
        yield session


@asynccontextmanager
async def get_db_ctx():
    async with async_session_factory() as session:
        yield session
