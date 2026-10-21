"""Unit tests for the 公众号资讯 sync (freepublish/batchget → gzh_articles)."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

import app.services.gzh_article as gzh_module
from app.port.config import settings


def _payload() -> dict:
    ts_b = int(datetime(2026, 10, 21, 8, 0, tzinfo=timezone.utc).timestamp())
    ts_a = int(datetime(2026, 10, 19, 8, 0, tzinfo=timezone.utc).timestamp())
    return {
        "total": 2,
        "item": [
            {
                "article_id": "p2",
                "update_time": ts_b,
                "content": {
                    "news_item": [
                        {
                            # 真实接口返回 http:// 开头，必须被升级为 https
                            "title": "推文B",
                            "url": "http://mp.weixin.qq.com/s/b",
                            "thumb_url": "https://mmbiz.example.com/x.jpg",
                        },
                        {
                            # 次条：条目级 update_time 兜底，无缩略图
                            "title": "推文A",
                            "url": "https://mp.weixin.qq.com/s/a",
                            "update_time": ts_a,
                        },
                        {"title": "没有链接的占位项", "url": ""},
                    ]
                },
            },
            {
                "article_id": "p1",
                "update_time": ts_a,
                "content": {
                    "news_item": [
                        {"title": "旧推文", "url": "https://mp.weixin.qq.com/s/old"},
                        # 与推文B 同链接（同为 http）的重复项，升级 https 后必须被去重
                        {"title": "重复项", "url": "http://mp.weixin.qq.com/s/b"},
                    ]
                },
            },
        ],
    }


def test_parse_orders_desc_dedups_and_filters() -> None:
    articles = gzh_module.parse_freepublish_response(_payload())

    assert [a["title"] for a in articles] == ["推文B", "推文A", "旧推文"]
    assert articles[0]["publish_time"] == datetime(2026, 10, 21, 8, 0, tzinfo=timezone.utc)
    assert articles[0]["thumb_url"] == "https://mmbiz.example.com/x.jpg"
    assert articles[1]["publish_time"] == datetime(2026, 10, 19, 8, 0, tzinfo=timezone.utc)
    assert articles[1]["thumb_url"] is None
    # 旧推文自身无时间，兜底继承条目级 update_time
    assert articles[2]["publish_time"] == datetime(2026, 10, 19, 8, 0, tzinfo=timezone.utc)


def test_parse_empty_payload_yields_nothing() -> None:
    assert gzh_module.parse_freepublish_response({}) == []
    assert gzh_module.parse_freepublish_response({"item": []}) == []


@pytest.mark.asyncio
async def test_sync_skips_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未配置 GZH 凭据（骨架态）时必须直接跳过，不得发起任何请求。"""

    monkeypatch.setattr(settings, "GZH_APPID", "")
    monkeypatch.setattr(settings, "GZH_APP_SECRET", "")

    class _Boom:
        def __init__(self) -> None:
            raise AssertionError("未配置凭据时不应创建客户端")

    monkeypatch.setattr(gzh_module, "GzhClient", _Boom)

    assert await gzh_module.sync_gzh_articles_once() == -1


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "_FakeResult":
        return self

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    def __init__(self, existing: list[Any]) -> None:
        self._existing = existing
        self.added: list[Any] = []
        self.commits = 0
        self.prune_executed = False

    async def execute(self, stmt: Any) -> _FakeResult:
        from sqlalchemy import Delete

        if isinstance(stmt, Delete):
            self.prune_executed = True
            return _FakeResult([])
        return _FakeResult(self._existing)

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1


@pytest.mark.asyncio
async def test_sync_upserts_new_and_existing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "GZH_APPID", "wx-gzh-app")
    monkeypatch.setattr(settings, "GZH_APP_SECRET", "secret")

    class _StubClient:
        async def fetch_published_articles(self, count: int = 20) -> dict:
            return _payload()

    monkeypatch.setattr(gzh_module, "GzhClient", _StubClient)

    existing = SimpleNamespace(
        id=9, title="旧标题", url="https://mp.weixin.qq.com/s/b",
        thumb_url=None, publish_time=None,
    )
    session = _FakeSession([existing])

    @asynccontextmanager
    async def _ctx():
        yield session

    monkeypatch.setattr(gzh_module, "get_db_ctx", _ctx)

    count = await gzh_module.sync_gzh_articles_once()

    assert count == 3
    assert existing.title == "推文B"  # 已存在的链接 → 原地更新
    assert sorted(a.url for a in session.added) == [
        "https://mp.weixin.qq.com/s/a",
        "https://mp.weixin.qq.com/s/old",
    ]
    assert session.prune_executed is True
    assert session.commits == 2
