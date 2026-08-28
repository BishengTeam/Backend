"""PostgreSQL integration tests for AdminCertProductService.get_stats.

Requires TEST_DATABASE_URL and TEST_DATABASE_URL_SYNC environment variables.
"""

import os
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


def _require_postgresql_urls() -> str:
    database_url = os.getenv("TEST_DATABASE_URL")
    database_url_sync = os.getenv("TEST_DATABASE_URL_SYNC")
    if not database_url or not database_url_sync:
        pytest.skip(
            "PostgreSQL integration tests require TEST_DATABASE_URL and TEST_DATABASE_URL_SYNC"
        )
    assert database_url.startswith("postgresql+asyncpg://")
    assert database_url_sync.startswith("postgresql://")
    os.environ.setdefault("DATABASE_URL", database_url)
    os.environ.setdefault("DATABASE_URL_SYNC", database_url_sync)
    os.environ.setdefault("JWT_SECRET", "test-secret")
    return database_url


@pytest.fixture
async def session_factory():
    database_url = _require_postgresql_urls()

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(database_url, pool_size=5, max_overflow=10)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
async def cert_stats_context(monkeypatch, session_factory):
    import importlib

    service_module = importlib.import_module("app.services.admin_cert_product")

    @asynccontextmanager
    async def test_db_ctx():
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(service_module, "get_db_ctx", test_db_ctx)

    unique = f"tst_{uuid4().hex[:10]}"
    code = f"{unique}_code"
    async with session_factory() as db:
        await db.execute(
            text(
                """
                INSERT INTO cert_product (type, code, name, chinese_name, is_active)
                VALUES (:type, :code, :name, :chinese_name, :is_active)
                """
            ),
            {
                "type": unique,
                "code": code,
                "name": unique,
                "chinese_name": unique,
                "is_active": True,
            },
        )
        await db.commit()
    try:
        yield unique
    finally:
        async with session_factory() as db:
            await db.execute(
                text("DELETE FROM cert_product WHERE type = :type"),
                {"type": unique},
            )
            await db.commit()


async def test_get_stats_groups_by_type_and_counts_active_products(
    cert_stats_context,
) -> None:
    from app.services.admin_cert_product import AdminCertProductService

    result = await AdminCertProductService().get_stats()

    mine = [item for item in result if item.type == cert_stats_context]
    assert len(mine) == 1
    stats = mine[0]
    assert stats.type_label == cert_stats_context
    assert stats.product_count == 1
    assert stats.active_product_count == 1
    assert stats.active_batch_count >= 0
    assert stats.total_enrolled >= 0
