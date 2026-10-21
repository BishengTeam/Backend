"""公众号推文定时同步：落库 gzh_articles，由 /api/zones 捎带给首页资讯栏。

- 未配置 GZH_APPID/GZH_APP_SECRET 时空转等待（骨架态，不刷错误日志）。
- 拉取失败保留上次数据，绝不清表。
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import delete, select

from app.adapter.database import get_db_ctx
from app.domain.content.src.model.gzh_article import GzhArticle
from app.integrations.gzh import GzhClient, is_configured

logger = logging.getLogger(__name__)

SYNC_INTERVAL_SECONDS = 30 * 60
UNCONFIGURED_INTERVAL_SECONDS = 10 * 60
# 每轮只对最新 FETCH_COUNT 条做 upsert；全表保留最近 KEEP_ROWS 条防膨胀
FETCH_COUNT = 20
KEEP_ROWS = 100


def _upgrade_https(url: str) -> str:
    """微信接口返回的文章链接是 http:// 开头，小程序 web-view 只允许 https，统一升级。"""
    if url.startswith("http://"):
        return "https://" + url[len("http://"):]
    return url


def parse_freepublish_response(payload: dict) -> list[dict]:
    """freepublish/batchget 响应 → [{title,url,thumb_url,publish_time}]，按发布时间倒序、按链接去重。"""
    items: list[dict] = []
    seen_urls: set[str] = set()
    for publish_item in payload.get("item") or []:
        if not isinstance(publish_item, dict):
            continue
        content = publish_item.get("content") or {}
        item_update_time = publish_item.get("update_time")
        for news in content.get("news_item") or []:
            if not isinstance(news, dict):
                continue
            title = (news.get("title") or "").strip()
            url = _upgrade_https((news.get("url") or news.get("link") or "").strip())
            if not title or not url or url in seen_urls:
                continue
            seen_urls.add(url)
            ts = news.get("update_time") or item_update_time
            publish_time = None
            if isinstance(ts, (int, float)) and ts > 0:
                publish_time = datetime.fromtimestamp(ts, tz=timezone.utc)
            items.append({
                "title": title,
                "url": url,
                "thumb_url": (news.get("thumb_url") or "").strip() or None,
                "publish_time": publish_time,
            })
    items.sort(
        key=lambda it: it["publish_time"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return items


async def sync_gzh_articles_once() -> int:
    """拉一次并 upsert。返回本次同步条数；-1 表示未配置凭据（骨架态）。"""
    if not is_configured():
        return -1
    payload = await GzhClient().fetch_published_articles(count=FETCH_COUNT)
    articles = parse_freepublish_response(payload)
    if not articles:
        return 0
    async with get_db_ctx() as db:
        rows = (
            await db.execute(
                select(GzhArticle).where(GzhArticle.url.in_([a["url"] for a in articles]))
            )
        ).scalars().all()
        by_url = {row.url: row for row in rows}
        for article in articles:
            row = by_url.get(article["url"])
            if row is None:
                db.add(GzhArticle(**article))
            else:
                row.title = article["title"]
                row.thumb_url = article["thumb_url"]
                row.publish_time = article["publish_time"]
        await db.commit()
        await _prune_stale(db)
    return len(articles)


async def _prune_stale(db) -> None:
    keep_ids = (
        select(GzhArticle.id)
        .order_by(GzhArticle.publish_time.desc().nullslast(), GzhArticle.id.desc())
        .limit(KEEP_ROWS)
    )
    await db.execute(delete(GzhArticle).where(GzhArticle.id.not_in(keep_ids)))
    await db.commit()


async def gzh_article_worker_loop() -> None:
    announced_unconfigured = False
    while True:
        if not is_configured():
            if not announced_unconfigured:
                logger.info("公众号资讯同步未配置 GZH_APPID/GZH_APP_SECRET，跳过（骨架态）")
                announced_unconfigured = True
            await asyncio.sleep(UNCONFIGURED_INTERVAL_SECONDS)
            continue
        announced_unconfigured = False
        started = time.monotonic()
        try:
            count = await sync_gzh_articles_once()
            logger.info("公众号资讯同步完成: %s 条, 耗时 %.1fs", count, time.monotonic() - started)
        except Exception as exc:
            logger.error("公众号资讯同步失败(保留上次数据): exception_type=%s", type(exc).__name__)
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)
