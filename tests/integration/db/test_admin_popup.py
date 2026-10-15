"""PostgreSQL integration tests for Admin Popup module.

Covers popup CRUD (mirrors test_admin_banner_zone.py's banner cases).
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
    prefix = f"pp_{uuid4().hex[:12]}"

    # patch get_db_ctx so services use test DB
    import app.services.admin_popup  # noqa: F811

    @asynccontextmanager
    async def _test_db_ctx():
        async with factory() as session:
            yield session

    monkeypatch.setattr(app.services.admin_popup, "get_db_ctx", _test_db_ctx)

    try:
        yield factory, prefix
    finally:
        from sqlalchemy import text

        async with factory() as db:
            pl = f"{prefix}%"
            await db.execute(text("DELETE FROM popup WHERE image_url LIKE :pl"), {"pl": pl})
            await db.commit()
        await engine.dispose()


async def test_create_popup(test_context):
    """调用 AdminPopupService().create() 返回对象含 id/image_url"""
    from app.schemas.admin_popup import PopupCreate
    from app.services.admin_popup import AdminPopupService

    factory, prefix = test_context

    result = await AdminPopupService().create(
        PopupCreate(image_url=f"{prefix}_img", sort=1)
    )
    assert result.id is not None, "Expected popup id to be set"
    assert result.image_url == f"{prefix}_img", (
        f"Expected image_url={prefix}_img, got {result.image_url}"
    )


async def test_list_popups(test_context):
    """创建后调 list_popups() 验证列表长度 >= 1"""
    from app.schemas.admin_popup import PopupCreate
    from app.services.admin_popup import AdminPopupService

    factory, prefix = test_context

    await AdminPopupService().create(
        PopupCreate(image_url=f"{prefix}_img", sort=1)
    )
    result = await AdminPopupService().list_popups()
    assert result.total >= 1, f"Expected at least 1 popup, got {result.total}"


async def test_update_popup(test_context):
    """创建后调 update(popup_id, PopupUpdate(...))，验证更新生效"""
    from app.schemas.admin_popup import PopupCreate, PopupUpdate
    from app.services.admin_popup import AdminPopupService

    factory, prefix = test_context

    svc = AdminPopupService()
    created = await svc.create(
        PopupCreate(image_url=f"{prefix}_img", sort=1)
    )

    updated = await svc.update(
        created.id,
        PopupUpdate(image_url=f"{prefix}_updated"),
    )
    assert updated.image_url == f"{prefix}_updated", (
        f"Expected updated image_url={prefix}_updated, got {updated.image_url}"
    )


async def test_delete_popup(test_context):
    """创建后调 delete(popup_id)，再 list 验证不出现"""
    from app.schemas.admin_popup import PopupCreate
    from app.services.admin_popup import AdminPopupService

    factory, prefix = test_context

    svc = AdminPopupService()
    created = await svc.create(
        PopupCreate(image_url=f"{prefix}_img", sort=1)
    )

    await svc.delete(created.id)

    result = await svc.list_popups(page=1, page_size=100)
    popup_ids = [p.id for p in result.items]
    assert created.id not in popup_ids, (
        f"Deleted popup {created.id} still present in list"
    )


async def test_batch_delete_popups(test_context):
    """创建 3 个 Popup → batch_delete → list 验证 prefix 相关 popup 消失"""
    from app.schemas.admin_popup import PopupCreate
    from app.services.admin_popup import AdminPopupService

    factory, prefix = test_context

    svc = AdminPopupService()
    created_ids = []
    for i in range(3):
        p = await svc.create(
            PopupCreate(image_url=f"{prefix}_batch_{i}", sort=i)
        )
        created_ids.append(p.id)

    deleted_count = await svc.batch_delete(created_ids)
    assert deleted_count == 3, (
        f"Expected 3 deleted, got {deleted_count}"
    )

    result = await svc.list_popups(page=1, page_size=100)
    popup_ids = {p.id for p in result.items}
    for cid in created_ids:
        assert cid not in popup_ids, (
            f"Popup {cid} still present after batch_delete"
        )
