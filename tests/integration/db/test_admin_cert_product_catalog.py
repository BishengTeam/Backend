"""PostgreSQL integration tests for cert product catalog.

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
async def catalog_context(monkeypatch, session_factory):
    import importlib

    service_module = importlib.import_module("app.services.admin_cert_product")

    @asynccontextmanager
    async def test_db_ctx():
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(service_module, "get_db_ctx", test_db_ctx)

    code = f"TST{uuid4().hex[:8].upper()}"
    catalog_id: int | None = None
    async with session_factory() as db:
        row = await db.execute(
            text(
                """
                INSERT INTO cert_product_catalog
                    (type, code, name, duration_minutes, question_count,
                     total_score, pass_score, cert_validity_years, retake_count,
                     prerequisite, remark, source)
                VALUES ('h3c', :code, '目录测试项', 60, 50, 1000, 600, 3, 0,
                        '无', '在线考试', 'test')
                RETURNING id
                """
            ),
            {"code": code},
        )
        catalog_id = row.scalar()
        await db.commit()
    try:
        yield code, catalog_id
    finally:
        async with session_factory() as db:
            await db.execute(
                text("DELETE FROM price_config WHERE product_type = :c"), {"c": code}
            )
            await db.execute(
                text("DELETE FROM cert_product WHERE code = :c"), {"c": code}
            )
            await db.execute(
                text("DELETE FROM cert_product_catalog WHERE id = :i"),
                {"i": catalog_id},
            )
            await db.commit()


async def test_list_catalog_marks_instantiated(catalog_context) -> None:
    from app.services.admin_cert_product import AdminCertProductService

    code, _catalog_id = catalog_context
    items = await AdminCertProductService().list_catalog("h3c")
    mine = [item for item in items if item.code == code]
    assert len(mine) == 1
    assert mine[0].instantiated is False
    assert mine[0].duration_minutes == 60
    assert mine[0].pass_score == 600


async def test_create_from_catalog_links_and_validates(catalog_context) -> None:
    from app.schemas.admin_cert_product import (
        CertProductCreate,
        CertProductPrice,
    )
    from app.services.admin_cert_product import AdminCertProductService

    code, catalog_id = catalog_context
    service = AdminCertProductService()

    created = await service.create(
        CertProductCreate(
            type="h3c",
            catalog_id=catalog_id,
            code=code,
            name=code.lower(),
            chinese_name="目录测试项",
            prices=[CertProductPrice(user_type="normal", price_cents=120000)],
        )
    )
    assert created.code == code
    assert created.prices[0].price_cents == 120000

    items = await service.list_catalog("h3c")
    mine = [item for item in items if item.code == code]
    assert mine[0].instantiated is True

    from app.port.exceptions import BusinessException

    with pytest.raises(BusinessException):
        await service.create(
            CertProductCreate(
                type="h3c",
                catalog_id=catalog_id,
                code="WRONG-CODE",
                name="x",
                chinese_name="x",
            )
        )


async def test_import_price_sheet_upserts_catalog_and_prices(
    catalog_context,
    session_factory,
) -> None:
    from io import BytesIO

    from openpyxl import Workbook
    from app.models.cert_product_catalog import CertProductCatalog
    from app.domain.order.src.index import PriceConfig
    from app.services.admin_cert_product import AdminCertProductService
    from sqlalchemy import select

    code = f"GB0T{uuid4().hex[:6].upper()}"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "新华三考试详情"
    worksheet.append([])
    worksheet.append([
        None, None, "导入测试认证", None, code, "原价", 1200,
        1, "无", 60, 50, 1000, 600, 3, None, "导入测试",
    ])
    worksheet.append([None, None, None, None, None, "网院优惠券", 500])
    content = BytesIO()
    workbook.save(content)
    content.seek(0)

    service = AdminCertProductService()
    first = await service.import_h3c_catalog(
        content=content.read(), source="import-test.xlsx"
    )
    assert first.imported_count == 1
    assert first.created_count == 1
    assert first.price_upserted_count == 2

    content.seek(0)
    second = await service.import_h3c_catalog(
        content=content.read(), source="import-test.xlsx"
    )
    assert second.created_count == 0
    assert second.updated_count == 1
    assert second.price_upserted_count == 0

    async with session_factory() as db:
        catalog = await db.scalar(
            select(CertProductCatalog).where(CertProductCatalog.code == code)
        )
        assert catalog is not None
        assert catalog.pass_score == 600
        assert catalog.source == "import-test.xlsx"
        prices = (
            await db.execute(
                select(PriceConfig).where(PriceConfig.product_type == code)
            )
        ).scalars().all()
        assert {row.user_type: row.price for row in prices} == {
            "normal": 120000,
            "student": 50000,
        }
        catalog_items = await service.list_catalog("h3c")
        imported = [item for item in catalog_items if item.code == code]
        assert {item.user_type: item.price_cents for item in imported[0].prices} == {
            "normal": 120000,
            "student": 50000,
        }
        await db.delete(catalog)
        for row in prices:
            await db.delete(row)
        await db.commit()


def test_import_script_parses_price_sheet() -> None:
    """导入脚本应能解析真实价格表并识别目标编码"""
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root))
    from scripts.import_cert_catalog import parse_sheet

    rows = parse_sheet(root / "docs" / "h3c" / "考试认证价格.xlsx")
    by_code = {row.code: row for row in rows}
    assert "GB0-192" in by_code
    assert by_code["GB0-192"].normal_price_yuan == 1200
    assert by_code["GB0-713"].student_price_yuan == 500
    assert by_code["GB0-192"].pass_score == 600
    assert by_code["GB0-170"].cert_validity_years == 3
