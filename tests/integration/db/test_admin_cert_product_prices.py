"""PostgreSQL integration tests for cert product price management.

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
async def price_context(monkeypatch, session_factory):
    import importlib

    service_module = importlib.import_module("app.services.admin_cert_product")

    @asynccontextmanager
    async def test_db_ctx():
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(service_module, "get_db_ctx", test_db_ctx)

    code = f"PRC{uuid4().hex[:10].upper()}"
    try:
        yield code
    finally:
        async with session_factory() as db:
            await db.execute(
                text("DELETE FROM price_config WHERE product_type = :c"),
                {"c": code},
            )
            await db.execute(
                text("DELETE FROM cert_product WHERE code = :c"),
                {"c": code},
            )
            await db.commit()


async def _create_with_prices(code: str, prices: list) -> None:
    from app.schemas.admin_cert_product import CertProductCreate
    from app.services.admin_cert_product import AdminCertProductService

    await AdminCertProductService().create(
        CertProductCreate(
            type="h3c",
            code=code,
            name=code.lower(),
            chinese_name="价格测试产品",
            prices=prices,
        )
    )


async def test_create_product_persists_prices(price_context) -> None:
    from app.schemas.admin_cert_product import CertProductPrice
    from app.services.admin_cert_product import AdminCertProductService

    await _create_with_prices(
        price_context,
        [
            CertProductPrice(user_type="student", price_cents=19900),
            CertProductPrice(user_type="normal", price_cents=29900),
        ],
    )

    product = await AdminCertProductService().get_by_code(price_context)
    by_tier = {price.user_type: price.price_cents for price in product.prices}
    assert by_tier == {"student": 19900, "normal": 29900}


async def test_update_replaces_price_tiers(price_context) -> None:
    from app.schemas.admin_cert_product import (
        CertProductPrice,
        CertProductUpdate,
    )
    from app.services.admin_cert_product import AdminCertProductService

    await _create_with_prices(
        price_context,
        [
            CertProductPrice(user_type="student", price_cents=19900),
            CertProductPrice(user_type="normal", price_cents=29900),
        ],
    )

    updated = await AdminCertProductService().update(
        price_context,
        CertProductUpdate(
            prices=[CertProductPrice(user_type="student", price_cents=15900)]
        ),
    )

    by_tier = {price.user_type: price.price_cents for price in updated.prices}
    assert by_tier == {"student": 15900}


async def test_list_products_returns_prices(price_context) -> None:
    from app.schemas.admin_cert_product import CertProductPrice
    from app.services.admin_cert_product import AdminCertProductService

    await _create_with_prices(
        price_context,
        [CertProductPrice(user_type="student", price_cents=25900)],
    )

    page = await AdminCertProductService().list_products("h3c", None, 1, 200)
    mine = [item for item in page.items if item.code == price_context]
    assert len(mine) == 1
    assert [(p.user_type, p.price_cents) for p in mine[0].prices] == [
        ("student", 25900)
    ]
