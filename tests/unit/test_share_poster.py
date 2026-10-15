"""分享海报（share_poster）：小程序端取 active 海报的服务行为。

「分享好友」页依赖 GET /api/share/poster 返回后台配置的推广海报；
未配置或全部下架时 image_url 必须为 null（前端据此置灰入口）。
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

import app.services.share as share_module
from app.services.share import ShareService


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "_FakeResult":
        return self

    def first(self) -> Any | None:
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    async def execute(self, stmt: Any) -> _FakeResult:
        return _FakeResult(self._rows)


def _patch_db(monkeypatch: pytest.MonkeyPatch, session: _FakeSession) -> None:
    @asynccontextmanager
    async def _ctx():
        yield session

    monkeypatch.setattr(share_module, "get_db_ctx", _ctx)


@pytest.mark.asyncio
async def test_get_active_poster_returns_latest_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    poster = SimpleNamespace(id=7, image_url="/api/media/share-poster.jpg", is_active=True)
    _patch_db(monkeypatch, _FakeSession([poster]))

    result = await ShareService().get_active_poster()

    assert result.image_url == "/api/media/share-poster.jpg"


@pytest.mark.asyncio
async def test_get_active_poster_returns_null_when_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_db(monkeypatch, _FakeSession([]))

    result = await ShareService().get_active_poster()

    assert result.image_url is None


@pytest.mark.asyncio
async def test_get_active_poster_ignores_inactive_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """service 只查 is_active 行：fake 只回 active 集，inactive 行不会出现在结果里"""
    poster = SimpleNamespace(id=3, image_url="/api/media/only-active.jpg", is_active=True)
    _patch_db(monkeypatch, _FakeSession([poster]))

    result = await ShareService().get_active_poster()

    assert result.image_url == "/api/media/only-active.jpg"
