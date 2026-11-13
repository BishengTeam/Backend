"""PostgreSQL integration tests for Admin 公众号资讯 module (gzh_articles).

Covers manual CRUD, duplicate-URL conflict, soft-delete revive,
batch import dedup/skip/revive, and restore (mirrors test_admin_popup.py).
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
    prefix = f"gz_{uuid4().hex[:12]}"

    # patch get_db_ctx so services use test DB
    import app.services.admin_gzh_article  # noqa: F811

    @asynccontextmanager
    async def _test_db_ctx():
        async with factory() as session:
            yield session

    monkeypatch.setattr(app.services.admin_gzh_article, "get_db_ctx", _test_db_ctx)

    try:
        yield factory, prefix
    finally:
        from sqlalchemy import text

        async with factory() as db:
            await db.execute(
                text("DELETE FROM gzh_articles WHERE url LIKE :pl"), {"pl": f"{prefix}%"}
            )
            await db.commit()
        await engine.dispose()


def _url(prefix: str, tag: str) -> str:
    return f"https://mp.weixin.qq.com/s/{prefix}-{tag}"


async def _insert_row(factory, *, title: str, url: str, source: str = "sync") -> int:
    """直接插一行（默认 sync 来源），返回 id。"""
    from app.domain.content.src.index import GzhArticle

    async with factory() as db:
        row = GzhArticle(title=title, url=url, source=source)
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row.id


async def test_create_manual_article(test_context):
    """新增返回 source=manual 且 id 已生成"""
    from app.schemas.admin_gzh_article import GzhArticleCreate
    from app.services.admin_gzh_article import AdminGzhArticleService

    _, prefix = test_context

    result = await AdminGzhArticleService().create(
        GzhArticleCreate(title=f"{prefix} 手动文章", url=_url(prefix, "a"))
    )
    assert result.id is not None, "Expected gzh article id to be set"
    assert result.source == "manual", f"Expected source=manual, got {result.source}"


async def test_create_duplicate_url_conflicts(test_context):
    """同链接活跃行已存在时 create 必须报冲突"""
    from app.port.exceptions import ConflictException
    from app.schemas.admin_gzh_article import GzhArticleCreate
    from app.services.admin_gzh_article import AdminGzhArticleService

    _, prefix = test_context
    url = _url(prefix, "dup")
    svc = AdminGzhArticleService()
    await svc.create(GzhArticleCreate(title=f"{prefix} 第一篇", url=url))

    with pytest.raises(ConflictException):
        await svc.create(GzhArticleCreate(title=f"{prefix} 第二篇", url=url))


async def test_create_revives_soft_deleted_row(test_context):
    """链接命中回收站软删行时复活为同一行并覆盖字段"""
    from app.schemas.admin_gzh_article import GzhArticleCreate
    from app.services.admin_gzh_article import AdminGzhArticleService

    _, prefix = test_context
    url = _url(prefix, "revive")
    svc = AdminGzhArticleService()
    created = await svc.create(GzhArticleCreate(title=f"{prefix} 旧标题", url=url))
    await svc.delete(created.id)

    revived = await svc.create(GzhArticleCreate(title=f"{prefix} 新标题", url=url))
    assert revived.id == created.id, "Expected the same row to be revived"
    assert revived.title == f"{prefix} 新标题"
    assert revived.source == "manual"

    result = await svc.list_articles(keyword=prefix, page=1, page_size=50)
    assert created.id in {r.id for r in result.items}, "Revived row must be listed again"


async def test_update_marks_manual_and_keyword_search(test_context):
    """sync 来源行经 Admin 编辑后转为 manual；keyword 按标题过滤"""
    from app.schemas.admin_gzh_article import GzhArticleUpdate
    from app.services.admin_gzh_article import AdminGzhArticleService

    factory, prefix = test_context
    row_id = await _insert_row(
        factory, title=f"{prefix} 同步行", url=_url(prefix, "sync")
    )

    svc = AdminGzhArticleService()
    updated = await svc.update(row_id, GzhArticleUpdate(title=f"{prefix} 改后标题"))
    assert updated.source == "manual", "Edited row must become source=manual"

    hit = await svc.list_articles(keyword="改后标题", page=1, page_size=50)
    assert row_id in {r.id for r in hit.items}

    miss = await svc.list_articles(keyword="绝不匹配的关键词xyz", page=1, page_size=50)
    assert row_id not in {r.id for r in miss.items}


async def test_delete_and_restore(test_context):
    """软删后列表不可见，恢复后重新可见"""
    from app.schemas.admin_gzh_article import GzhArticleCreate
    from app.services.admin_gzh_article import AdminGzhArticleService

    _, prefix = test_context
    svc = AdminGzhArticleService()
    created = await svc.create(
        GzhArticleCreate(title=f"{prefix} 删恢复", url=_url(prefix, "del"))
    )

    await svc.delete(created.id)
    after_delete = await svc.list_articles(keyword=prefix, page=1, page_size=100)
    assert created.id not in {r.id for r in after_delete.items}

    await svc.restore(created.id)
    after_restore = await svc.list_articles(keyword=prefix, page=1, page_size=100)
    assert created.id in {r.id for r in after_restore.items}


async def test_batch_import_dedup_and_skip(test_context):
    """批量导入：payload 内重复跳过、库中活跃行跳过、新行照常入库"""
    from app.schemas.admin_gzh_article import (
        GzhArticleBatchImportRequest,
        GzhArticleImportItem,
    )
    from app.services.admin_gzh_article import AdminGzhArticleService

    factory, prefix = test_context
    existing_url = _url(prefix, "exists")
    await _insert_row(factory, title=f"{prefix} 库里已有", url=existing_url)

    url_a = _url(prefix, "batch-a")
    items = [
        GzhArticleImportItem(title=f"{prefix} 新文章", url=url_a),
        GzhArticleImportItem(title=f"{prefix} 本轮重复", url=url_a),
        GzhArticleImportItem(title=f"{prefix} 撞库", url=existing_url),
    ]
    result = await AdminGzhArticleService().batch_import(
        GzhArticleBatchImportRequest(items=items)
    )
    assert result.created == 1, f"Expected 1 created, got {result.created}"
    assert result.skipped == 2, f"Expected 2 skipped, got {result.skipped}"
    assert result.errors == []


async def test_batch_import_revives_soft_deleted(test_context):
    """批量导入命中软删行 → 复活并覆盖字段，计入 created"""
    from app.schemas.admin_gzh_article import (
        GzhArticleBatchImportRequest,
        GzhArticleCreate,
        GzhArticleImportItem,
    )
    from app.services.admin_gzh_article import AdminGzhArticleService

    _, prefix = test_context
    url = _url(prefix, "import-revive")
    svc = AdminGzhArticleService()
    created = await svc.create(GzhArticleCreate(title=f"{prefix} 原标题", url=url))
    await svc.delete(created.id)

    result = await svc.batch_import(
        GzhArticleBatchImportRequest(
            items=[GzhArticleImportItem(title=f"{prefix} 导入新标题", url=url)]
        )
    )
    assert result.created == 1
    assert result.skipped == 0

    listed = await svc.list_articles(keyword=prefix, page=1, page_size=50)
    row = next(r for r in listed.items if r.id == created.id)
    assert row.title == f"{prefix} 导入新标题"
    assert row.source == "manual"


async def test_batch_delete(test_context):
    """批量软删返回实际条数且列表不可见"""
    from app.schemas.admin_gzh_article import GzhArticleCreate
    from app.services.admin_gzh_article import AdminGzhArticleService

    _, prefix = test_context
    svc = AdminGzhArticleService()
    ids = []
    for i in range(3):
        row = await svc.create(
            GzhArticleCreate(title=f"{prefix} 批量 {i}", url=_url(prefix, f"bd{i}"))
        )
        ids.append(row.id)

    deleted = await svc.batch_delete(ids)
    assert deleted == 3, f"Expected 3 deleted, got {deleted}"

    result = await svc.list_articles(keyword=prefix, page=1, page_size=100)
    assert not {r.id for r in result.items} & set(ids)


async def test_fetch_meta_existing_url_short_circuits(test_context):
    """链接已在库中时 fetch-meta 直接返回库内条目（exists=true），不触发网络抓取"""
    from app.schemas.admin_gzh_article import GzhArticleFetchMetaRequest
    from app.services.admin_gzh_article import AdminGzhArticleService

    _, prefix = test_context
    url = _url(prefix, "meta")
    svc = AdminGzhArticleService()
    await svc.create(
        GzhArticleCreate(title=f"{prefix} 库内标题", url=url, thumb_url="https://mmbiz.qpic.cn/x.jpg")
    )

    result = await svc.fetch_meta(GzhArticleFetchMetaRequest(url=url).url)
    assert result.exists is True, "Expected exists=true for stored URL"
    assert result.source == "manual"
    assert result.title == f"{prefix} 库内标题"
    assert result.thumb_url == "https://mmbiz.qpic.cn/x.jpg"
