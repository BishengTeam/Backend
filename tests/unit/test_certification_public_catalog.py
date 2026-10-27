"""Execute public catalog queries against isolated SQL tables, without external I/O."""

from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.adapter.database import Base
from app.domain.certification.src.index import Certification
from app.models.cert_product import CertProduct
from app.models.cert_product_catalog import CertProductCatalog
from app.port.exceptions import NotFoundException
from app.schemas.certification import CertificationFilter
from app.services.certification import CERT_PRODUCT_VENDOR_MAP, CertificationService


@pytest.fixture
async def catalog(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(lambda db: Base.metadata.create_all(db, tables=[
                CertProductCatalog.__table__, CertProduct.__table__, Certification.__table__,
            ]))
        factory = async_sessionmaker(engine, expire_on_commit=False)

        @asynccontextmanager
        async def context():
            async with factory() as db:
                yield db

        monkeypatch.setattr("app.services.certification.get_db_ctx", context)
        yield CertificationService(), factory
    finally:
        await engine.dispose()


def legacy(vendor, code="legacy-only"):
    return Certification(id=1000, name=code, chinese_name="历史产品", code=code,
                         vendor=vendor, is_active=True, requires_xuexin=False, pay_first=True)


def product(kind, **overrides):
    values = dict(type=kind, code="SCSA-T" if kind == "sangfor" else "CURRENT",
                  name="current", chinese_name="当前产品", is_active=True,
                  renshe_status="available" if kind == "renshe" else None)
    values.update(overrides)
    return CertProduct(**values)


@pytest.mark.parametrize("vendor", CERT_PRODUCT_VENDOR_MAP.values())
async def test_managed_legacy_only_products_are_not_public(catalog, vendor):
    service, factory = catalog
    async with factory() as db:
        db.add(legacy(vendor))
        await db.commit()
    assert await service.list_certifications() == []
    assert await service.list_certifications(CertificationFilter(vendor=vendor)) == []
    with pytest.raises(NotFoundException):
        await service.get_detail(1000)


@pytest.mark.parametrize("kind,vendor", CERT_PRODUCT_VENDOR_MAP.items())
async def test_current_product_controls_visibility_and_legacy_alias(catalog, kind, vendor):
    service, factory = catalog
    current = product(kind)
    async with factory() as db:
        db.add_all([current, legacy(vendor, current.code)])
        await db.commit()
    # Product visibility does not claim there is an open batch.
    assert [row.id for row in await service.list_certifications()] == [current.id]
    assert [row.id for row in await service.list_certifications(CertificationFilter(vendor=vendor))] == [current.id]
    assert (await service.get_detail(1000)).id == current.id
    async with factory() as db:
        row = await db.get(CertProduct, current.id)
        row.is_active = False
        await db.commit()
    assert await service.list_certifications() == []
    for item_id in (current.id, 1000):
        with pytest.raises(NotFoundException):
            await service.get_detail(item_id)


@pytest.mark.parametrize("kind,overrides", [
    ("sangfor", {"code": "SF-CSE"}),
    ("renshe", {"renshe_status": "draft"}),
    ("renshe", {"renshe_status": None}),
])
async def test_dedicated_catalog_eligibility_also_applies_to_general_catalog(catalog, kind, overrides):
    service, factory = catalog
    current = product(kind, **overrides)
    async with factory() as db:
        db.add_all([current, legacy(CERT_PRODUCT_VENDOR_MAP[kind], current.code)])
        await db.commit()
    assert await service.list_certifications() == []
    for item_id in (current.id, 1000):
        with pytest.raises(NotFoundException):
            await service.get_detail(item_id)


async def test_unmanaged_vendor_retains_legacy_compatibility(catalog):
    service, factory = catalog
    async with factory() as db:
        db.add(legacy("SmokeVendor"))
        await db.commit()
    assert [row.id for row in await service.list_certifications()] == [1000]
    assert (await service.get_detail(1000)).code == "legacy-only"
