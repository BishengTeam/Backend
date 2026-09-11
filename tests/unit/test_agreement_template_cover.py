"""Unit tests for agreement cover rendering and backfill behavior."""

from __future__ import annotations

import asyncio
import argparse
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.admin_agreement_template as admin_module
import app.services.agreement_template_cover as cover_module
from app.domain.content.src.index import AgreementTemplate
from app.services.admin_agreement_template import AdminAgreementTemplateService
from app.services.agreement_template_cover import (
    AgreementCoverService,
    build_agreement_cover_html,
)


def test_cover_html_escapes_title_and_uses_offline_inline_layout() -> None:
    html = build_agreement_cover_html("用户 & 权益", "<p>正文</p>")

    assert "用户 &amp; 权益" in html
    assert "<p>正文</p>" in html
    assert "http://" not in html
    assert "https://" not in html
    assert "font-family:" in html


def test_cover_renderer_uses_fixed_viewport_jpeg_and_blocks_resources(monkeypatch):
    page = SimpleNamespace(
        set_default_timeout=MagicMock(),
        route=AsyncMock(),
        set_content=AsyncMock(),
        screenshot=AsyncMock(return_value=b"jpeg-bytes"),
    )
    context = SimpleNamespace(
        new_page=AsyncMock(return_value=page),
        close=AsyncMock(),
    )
    browser = SimpleNamespace(
        new_context=AsyncMock(return_value=context),
        close=AsyncMock(),
    )
    playwright = SimpleNamespace(
        chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)),
    )

    @asynccontextmanager
    async def fake_async_playwright():
        yield playwright

    monkeypatch.setattr(cover_module, "async_playwright", fake_async_playwright)

    result = asyncio.run(AgreementCoverService()._render("标题", "<p>正文</p>"))
    assert result == b"jpeg-bytes"

    browser.new_context.assert_awaited_once_with(
        viewport={"width": 720, "height": 960},
        device_scale_factor=2,
        java_script_enabled=False,
    )
    page.set_content.assert_awaited_once_with(
        cover_module.build_agreement_cover_html("标题", "<p>正文</p>"),
        wait_until="domcontentloaded",
    )
    page.screenshot.assert_awaited_once_with(
        type="jpeg",
        quality=90,
        full_page=False,
        animations="disabled",
    )
    assert page.route.await_args.args[0] == "**/*"
    route = SimpleNamespace(abort=AsyncMock())
    route_handler = page.route.await_args.args[1]
    asyncio.run(route_handler(route))
    route.abort.assert_awaited_once_with()
    assert context.close.await_count == 1
    assert browser.close.await_count == 1


def test_cover_generate_saves_jpeg_bytes(monkeypatch):
    service = AgreementCoverService()
    monkeypatch.setattr(AgreementCoverService, "_render_lock", asyncio.Lock())
    save_bytes = MagicMock(return_value={"url": "/api/media/generated.jpg"})
    monkeypatch.setattr(cover_module.UploadService, "save_bytes", save_bytes)
    monkeypatch.setattr(
        service,
        "_render",
        AsyncMock(return_value=b"jpeg-bytes"),
    )

    result = asyncio.run(service.generate("标题", "<p>正文</p>"))

    assert result == "/api/media/generated.jpg"
    save_bytes.assert_called_once_with(b"jpeg-bytes", ".jpg")


def test_cover_generate_serializes_render_jobs(monkeypatch):
    active = 0
    max_active = 0

    async def render(title: str, content: str) -> bytes:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0)
        active -= 1
        return b"image"

    monkeypatch.setattr(AgreementCoverService, "_render", staticmethod(render))
    monkeypatch.setattr(AgreementCoverService, "_render_lock", asyncio.Lock())
    monkeypatch.setattr(
        cover_module.UploadService,
        "save_bytes",
        MagicMock(return_value={"url": "/api/media/cover.jpg"}),
    )

    async def collect_results() -> list[str]:
        return await asyncio.gather(
            *[AgreementCoverService().generate("标题", "正文") for _ in range(3)]
        )

    results = asyncio.run(collect_results())

    assert results == ["/api/media/cover.jpg"] * 3
    assert max_active == 1


def test_cover_generate_times_out(monkeypatch):
    async def render(title: str, content: str) -> bytes:
        await asyncio.sleep(0.05)
        return b"image"

    monkeypatch.setattr(cover_module, "COVER_GENERATION_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(AgreementCoverService, "_render", staticmethod(render))
    monkeypatch.setattr(AgreementCoverService, "_render_lock", asyncio.Lock())

    with pytest.raises(TimeoutError):
        asyncio.run(AgreementCoverService().generate("标题", "正文"))


def test_attach_cover_failure_does_not_raise(monkeypatch):
    row = AgreementTemplate(
        type="privacy",
        title="隐私政策",
        content="<p>正文</p>",
        version=1,
        status="active",
    )
    row.id = 7

    db = SimpleNamespace(
        get=AsyncMock(return_value=row),
        execute=AsyncMock(),
        commit=AsyncMock(),
    )

    @asynccontextmanager
    async def fake_db_ctx():
        yield db

    monkeypatch.setattr(admin_module, "get_db_ctx", fake_db_ctx)
    monkeypatch.setattr(
        admin_module.AgreementCoverService,
        "generate",
        AsyncMock(side_effect=RuntimeError("chromium unavailable")),
    )

    asyncio.run(AdminAgreementTemplateService._attach_cover(7))

    assert row.cover_url is None


def test_attach_cover_success_writes_only_missing_cover(monkeypatch):
    row = AgreementTemplate(
        type="privacy",
        title="隐私政策",
        content="<p>正文</p>",
        version=1,
        status="active",
    )
    row.id = 7

    async def mark_cover_updated(statement) -> SimpleNamespace:
        row.cover_url = "/api/media/new-cover.jpg"
        return SimpleNamespace(rowcount=1)

    db = SimpleNamespace(
        get=AsyncMock(return_value=row),
        execute=AsyncMock(side_effect=mark_cover_updated),
        commit=AsyncMock(),
    )

    @asynccontextmanager
    async def fake_db_ctx():
        yield db

    monkeypatch.setattr(admin_module, "get_db_ctx", fake_db_ctx)
    monkeypatch.setattr(
        admin_module.AgreementCoverService,
        "generate",
        AsyncMock(return_value="/api/media/new-cover.jpg"),
    )
    monkeypatch.setattr(
        admin_module.UploadService,
        "delete_file_by_url",
        MagicMock(),
    )

    asyncio.run(AdminAgreementTemplateService._attach_cover(7))

    assert db.execute.await_count == 1
    assert db.commit.await_count == 1


def test_attach_cover_does_not_generate_when_cover_already_exists(monkeypatch):
    row = AgreementTemplate(
        type="privacy",
        title="隐私政策",
        content="<p>正文</p>",
        version=1,
        status="active",
        cover_url="/api/media/existing.jpg",
    )
    row.id = 7
    db = SimpleNamespace(
        get=AsyncMock(return_value=row),
        execute=AsyncMock(),
        commit=AsyncMock(),
    )

    @asynccontextmanager
    async def fake_db_ctx():
        yield db

    monkeypatch.setattr(admin_module, "get_db_ctx", fake_db_ctx)
    generate = AsyncMock()
    monkeypatch.setattr(
        admin_module.AgreementCoverService,
        "generate",
        generate,
    )

    asyncio.run(AdminAgreementTemplateService._attach_cover(7))

    generate.assert_not_called()
    db.execute.assert_not_called()
    db.commit.assert_not_called()


def test_create_generates_cover_after_template_commit(monkeypatch):
    row = AgreementTemplate(
        type="privacy",
        title="隐私政策",
        content="<p>正文</p>",
        version=1,
        status="active",
    )
    row.id = 7
    row.created_at = datetime(2026, 9, 11, 10, 0)
    row.updated_at = datetime(2026, 9, 11, 10, 0)

    async def mark_cover_updated(statement) -> SimpleNamespace:
        row.cover_url = "/api/media/v1.jpg"
        return SimpleNamespace(rowcount=1)

    db = SimpleNamespace(
        get=AsyncMock(return_value=row),
        execute=AsyncMock(side_effect=mark_cover_updated),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )

    @asynccontextmanager
    async def fake_db_ctx():
        yield db

    monkeypatch.setattr(admin_module, "get_db_ctx", fake_db_ctx)
    monkeypatch.setattr(
        AdminAgreementTemplateService,
        "_new_version",
        AsyncMock(return_value=row),
    )
    monkeypatch.setattr(
        admin_module.AgreementCoverService,
        "generate",
        AsyncMock(return_value="/api/media/v1.jpg"),
    )

    result = asyncio.run(
        AdminAgreementTemplateService().create(
            admin_module.AdminAgreementTemplateCreate(
                type="privacy", title="隐私政策", content="<p>正文</p>"
            )
        )
    )

    assert row.cover_url == "/api/media/v1.jpg"
    assert result.cover_url == "/api/media/v1.jpg"
    assert db.commit.await_count == 2


def test_update_creates_new_version_cover_and_preserves_old_cover(monkeypatch):
    old = AgreementTemplate(
        type="privacy",
        title="隐私政策 v1",
        content="<p>v1</p>",
        version=1,
        status="active",
        cover_url="/api/media/v1.jpg",
    )
    old.id = 7
    old.created_at = datetime(2026, 9, 11, 9, 0)
    old.updated_at = datetime(2026, 9, 11, 9, 0)
    new = AgreementTemplate(
        type="privacy",
        title="隐私政策 v2",
        content="<p>v2</p>",
        version=2,
        status="active",
    )
    new.id = 8
    new.created_at = datetime(2026, 9, 11, 10, 0)
    new.updated_at = datetime(2026, 9, 11, 10, 0)

    async def mark_cover_updated(statement) -> SimpleNamespace:
        new.cover_url = "/api/media/v2.jpg"
        return SimpleNamespace(rowcount=1)

    async def fake_new_version(db, *, agreement_type, title, content):
        assert agreement_type == "privacy"
        old.status = "archived"
        return new

    db = SimpleNamespace(
        get=AsyncMock(side_effect=[old, new, new, new]),
        execute=AsyncMock(side_effect=mark_cover_updated),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )

    @asynccontextmanager
    async def fake_db_ctx():
        yield db

    monkeypatch.setattr(admin_module, "get_db_ctx", fake_db_ctx)
    monkeypatch.setattr(
        AdminAgreementTemplateService,
        "_new_version",
        staticmethod(fake_new_version),
    )
    monkeypatch.setattr(
        admin_module.AgreementCoverService,
        "generate",
        AsyncMock(return_value="/api/media/v2.jpg"),
    )

    result = asyncio.run(
        AdminAgreementTemplateService().update(
            7,
            admin_module.AdminAgreementTemplateUpdate(
                title="隐私政策 v2", content="<p>v2</p>"
            ),
        )
    )

    assert old.cover_url == "/api/media/v1.jpg"
    assert old.status == "archived"
    assert new.cover_url == "/api/media/v2.jpg"
    assert result.version == 2
    assert result.cover_url == "/api/media/v2.jpg"


def test_archive_does_not_generate_a_new_cover(monkeypatch):
    row = AgreementTemplate(
        type="privacy",
        title="隐私政策",
        content="<p>正文</p>",
        version=1,
        status="active",
        cover_url="/api/media/v1.jpg",
    )
    row.id = 7
    row.created_at = datetime(2026, 9, 11, 10, 0)
    row.updated_at = datetime(2026, 9, 11, 10, 0)
    db = SimpleNamespace(
        get=AsyncMock(return_value=row),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )

    @asynccontextmanager
    async def fake_db_ctx():
        yield db

    monkeypatch.setattr(admin_module, "get_db_ctx", fake_db_ctx)
    generate = AsyncMock()
    monkeypatch.setattr(
        admin_module.AgreementCoverService,
        "generate",
        generate,
    )

    result = asyncio.run(AdminAgreementTemplateService().archive(7))

    assert result.status == "archived"
    assert result.cover_url == "/api/media/v1.jpg"
    generate.assert_not_called()
