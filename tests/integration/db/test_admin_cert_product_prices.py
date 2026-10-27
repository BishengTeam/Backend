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

    code = f"GB0T{uuid4().hex[:10].upper()}"
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
            await db.execute(
                text("DELETE FROM cert_product_catalog WHERE code = :c"),
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


async def test_h3c_create_without_prices_needs_no_price_configuration(price_context) -> None:
    from app.schemas.admin_cert_product import CertProductCreate
    from app.services.admin_cert_product import AdminCertProductService

    created = await AdminCertProductService().create(CertProductCreate(
        type="h3c", code=price_context, name=price_context.lower(), chinese_name="仅展示信息的认证产品",
    ))
    assert created.code == price_context
    assert created.prices == []


async def test_h3c_create_and_edit_without_prices_preserve_imported_references(price_context, session_factory) -> None:
    from io import BytesIO
    from openpyxl import Workbook
    from sqlalchemy import select
    from app.domain.order.src.index import PriceConfig
    from app.schemas.admin_cert_product import CertProductCreate, CertProductUpdate
    from app.services.admin_cert_product import AdminCertProductService

    book = Workbook()
    sheet = book.active
    sheet.title = "新华三考试详情"
    sheet.append([None, None, "目录参考价测试", None, price_context, "原价", 1200,
        1, "无", 60, 50, 1000, 600, 3, None, "厂商参考价"])
    sheet.append([None, None, None, None, None, "网院优惠券", 500])
    content = BytesIO()
    book.save(content)
    service = AdminCertProductService()
    imported = await service.import_h3c_catalog(content=content.getvalue(), source="reference-test.xlsx")
    assert imported.price_upserted_count == 2

    created = await service.create(CertProductCreate(type="h3c", code=price_context,
        name=price_context.lower(), chinese_name="创建后保留历史参考价"))
    expected = {"normal": 120000, "student": 50000}
    assert {row.user_type: row.price_cents for row in created.prices} == expected
    updated = await service.update(price_context, CertProductUpdate(chinese_name="仅更新产品介绍", description="收费请在批次设置"))
    assert {row.user_type: row.price_cents for row in updated.prices} == expected
    async with session_factory() as db:
        rows = (await db.scalars(select(PriceConfig).where(PriceConfig.product_type == price_context))).all()
        assert {row.user_type: (row.price, row.is_active) for row in rows} == {
            tier: (value, True) for tier, value in expected.items()
        }


async def test_explicit_empty_prices_retains_legacy_deactivation_semantics(price_context, session_factory) -> None:
    from sqlalchemy import select
    from app.domain.order.src.index import PriceConfig
    from app.schemas.admin_cert_product import CertProductPrice, CertProductUpdate
    from app.services.admin_cert_product import AdminCertProductService

    await _create_with_prices(price_context, [CertProductPrice(user_type="normal", price_cents=120000)])
    updated = await AdminCertProductService().update(price_context, CertProductUpdate(prices=[]))
    assert updated.prices == []
    async with session_factory() as db:
        row = await db.scalar(select(PriceConfig).where(PriceConfig.product_type == price_context))
        assert row.price == 120000 and row.is_active is False


async def test_other_vendor_explicit_prices_remain_supported(price_context) -> None:
    from app.schemas.admin_cert_product import CertProductCreate, CertProductPrice, CertProductUpdate
    from app.services.admin_cert_product import AdminCertProductService

    service = AdminCertProductService()
    created = await service.create(CertProductCreate(type="nisp", code=price_context,
        name="nisp-price-test", chinese_name="其他认证价格兼容", prices=[CertProductPrice(user_type="normal", price_cents=45000)]))
    assert [(row.user_type, row.price_cents) for row in created.prices] == [("normal", 45000)]
    updated = await service.update(price_context, CertProductUpdate(prices=[CertProductPrice(user_type="normal", price_cents=55000)]))
    assert [(row.user_type, row.price_cents) for row in updated.prices] == [("normal", 55000)]
