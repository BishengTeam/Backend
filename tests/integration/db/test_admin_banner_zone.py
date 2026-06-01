"""PostgreSQL integration tests for Admin Banner and Zone modules.

Covers Banner CRUD and Zone status toggle.
"""

import os
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


@pytest.fixture
async def test_context(monkeypatch):
    """返回 (session_factory, prefix)，服务调用通过 monkeypatch 走测试库"""
    database_url = os.environ.get("TEST_DATABASE_URL")
    database_url_sync = os.environ.get("TEST_DATABASE_URL_SYNC")
    if not database_url or not database_url_sync:
        pytest.skip("Requires TEST_DATABASE_URL")

    os.environ.setdefault("DATABASE_URL", database_url)
    os.environ.setdefault("JWT_SECRET", "test-secret-for-integration-testing-min-32-chars")

    engine = create_async_engine(database_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"bz_{uuid4().hex[:12]}"

    # patch get_db_ctx so services use test DB
    import app.services.admin_banner  # noqa: F811
    import app.services.admin_zone   # noqa: F811

    @asynccontextmanager
    async def _test_db_ctx():
        async with factory() as session:
            yield session

    monkeypatch.setattr(app.services.admin_banner, "get_db_ctx", _test_db_ctx)
    monkeypatch.setattr(app.services.admin_zone, "get_db_ctx", _test_db_ctx)

    try:
        yield factory, prefix
    finally:
        from sqlalchemy import text

        async with factory() as db:
            pl = f"{prefix}%"
            await db.execute(text("DELETE FROM banner WHERE image_url LIKE :pl"), {"pl": pl})
            await db.execute(text("DELETE FROM zone WHERE title LIKE :pl"), {"pl": pl})
            await db.commit()
        await engine.dispose()


# ──────────────────────────────────────────────
# Banner CRUD
# ──────────────────────────────────────────────


async def test_create_banner(test_context):
    """调用 AdminBannerService().create() 返回对象含 id/image_url"""
    from app.schemas.admin_banner import BannerCreate
    from app.services.admin_banner import AdminBannerService

    factory, prefix = test_context

    result = await AdminBannerService().create(
        BannerCreate(image_url=f"{prefix}_img", sort=1)
    )
    assert result.id is not None, "Expected banner id to be set"
    assert result.image_url == f"{prefix}_img", (
        f"Expected image_url={prefix}_img, got {result.image_url}"
    )


async def test_list_banners(test_context):
    """创建后调 list_banners() 验证列表长度 >= 1"""
    from app.schemas.admin_banner import BannerCreate
    from app.services.admin_banner import AdminBannerService

    factory, prefix = test_context

    await AdminBannerService().create(
        BannerCreate(image_url=f"{prefix}_img", sort=1)
    )
    banners = await AdminBannerService().list_banners()
    assert len(banners) >= 1, f"Expected at least 1 banner, got {len(banners)}"


async def test_update_banner(test_context):
    """创建后调 update(banner_id, BannerUpdate(...))，验证更新生效"""
    from app.schemas.admin_banner import BannerCreate, BannerUpdate
    from app.services.admin_banner import AdminBannerService

    factory, prefix = test_context

    svc = AdminBannerService()
    created = await svc.create(
        BannerCreate(image_url=f"{prefix}_img", sort=1)
    )

    updated = await svc.update(
        created.id,
        BannerUpdate(image_url=f"{prefix}_updated"),
    )
    assert updated.image_url == f"{prefix}_updated", (
        f"Expected updated image_url={prefix}_updated, got {updated.image_url}"
    )


async def test_delete_banner(test_context):
    """创建后调 delete(banner_id)，再 list 验证不出现"""
    from app.schemas.admin_banner import BannerCreate
    from app.services.admin_banner import AdminBannerService

    factory, prefix = test_context

    svc = AdminBannerService()
    created = await svc.create(
        BannerCreate(image_url=f"{prefix}_img", sort=1)
    )

    await svc.delete(created.id)

    banners = await svc.list_banners()
    banner_ids = [b.id for b in banners]
    assert created.id not in banner_ids, (
        f"Deleted banner {created.id} still present in list"
    )


# ──────────────────────────────────────────────
# Zone status toggle
# ──────────────────────────────────────────────


async def test_toggle_zone_status(test_context):
    """创建 zone → toggle_status False → toggle_status True 验证状态切换"""
    from app.schemas.admin_zone import AdminZoneCreate
    from app.services.admin_zone import AdminZoneService

    factory, prefix = test_context

    svc = AdminZoneService()
    created = await svc.create(
        AdminZoneCreate(
            zone_type="test",
            title=f"{prefix}_z",
            cover_url=f"{prefix}_cover",
        )
    )

    # toggle off
    toggled_off = await svc.toggle_status(created.id, False)
    assert toggled_off.is_active is False, (
        f"Expected is_active=False after toggle off, got {toggled_off.is_active}"
    )

    # toggle on
    toggled_on = await svc.toggle_status(created.id, True)
    assert toggled_on.is_active is True, (
        f"Expected is_active=True after toggle on, got {toggled_on.is_active}"
    )


# ──────────────────────────────────────────────
# batch_delete / batch_deactivate / update_sort
# ──────────────────────────────────────────────


async def test_batch_delete_banners(test_context):
    """创建 3 个 Banner → batch_delete → list 验证 prefix 相关 banner 消失"""
    from app.schemas.admin_banner import BannerCreate
    from app.services.admin_banner import AdminBannerService

    factory, prefix = test_context

    svc = AdminBannerService()
    created_ids = []
    for i in range(3):
        b = await svc.create(
            BannerCreate(image_url=f"{prefix}_batch_{i}", sort=i)
        )
        created_ids.append(b.id)

    deleted_count = await svc.batch_delete(created_ids)
    assert deleted_count == 3, (
        f"Expected 3 deleted, got {deleted_count}"
    )

    banners = await svc.list_banners()
    banner_ids = {b.id for b in banners}
    for cid in created_ids:
        assert cid not in banner_ids, (
            f"Banner {cid} still present after batch_delete"
        )


async def test_batch_delete_zones(test_context):
    """创建 3 个 Zone → batch_deactivate → list 验证 is_active 均为 False"""
    from app.schemas.admin_zone import AdminZoneCreate
    from app.services.admin_zone import AdminZoneService

    factory, prefix = test_context

    svc = AdminZoneService()
    created_ids = []
    for i in range(3):
        z = await svc.create(
            AdminZoneCreate(
                zone_type="test",
                title=f"{prefix}_bz_{i}",
                cover_url=f"{prefix}_cover_{i}",
            )
        )
        created_ids.append(z.id)

    deactivated_count = await svc.batch_deactivate(created_ids)
    assert deactivated_count == 3, (
        f"Expected 3 deactivated, got {deactivated_count}"
    )

    result = await svc.list_zones(keyword=None, page=1, page_size=100)
    zone_map = {z.id: z for z in result.items}
    for cid in created_ids:
        assert cid in zone_map, f"Zone {cid} missing from list"
        assert zone_map[cid].is_active is False, (
            f"Zone {cid} is_active expected False, got {zone_map[cid].is_active}"
        )


async def test_update_zones_sort(test_context):
    """创建 2 个 Zone → update_sort → 重新查询验证 sort_order 已更新"""
    from app.schemas.admin_zone import AdminZoneCreate
    from app.services.admin_zone import AdminZoneService

    factory, prefix = test_context

    svc = AdminZoneService()
    z1 = await svc.create(
        AdminZoneCreate(
            zone_type="test",
            title=f"{prefix}_sort_1",
            cover_url=f"{prefix}_cover_1",
            sort_order=0,
        )
    )
    z2 = await svc.create(
        AdminZoneCreate(
            zone_type="test",
            title=f"{prefix}_sort_2",
            cover_url=f"{prefix}_cover_2",
            sort_order=0,
        )
    )

    updates = [
        {"id": z1.id, "sort_order": 10},
        {"id": z2.id, "sort_order": 20},
    ]
    updated_count = await svc.update_sort(updates)
    assert updated_count == 2, (
        f"Expected 2 updated, got {updated_count}"
    )

    result = await svc.list_zones(keyword=None, page=1, page_size=100)
    zone_map = {z.id: z for z in result.items}
    assert zone_map[z1.id].sort_order == 10, (
        f"Expected sort_order=10, got {zone_map[z1.id].sort_order}"
    )
    assert zone_map[z2.id].sort_order == 20, (
        f"Expected sort_order=20, got {zone_map[z2.id].sort_order}"
    )
