"""公众号推文定时同步：落库 gzh_articles，由 /api/zones 捎带给首页资讯栏。

- 未配置 GZH_APPID/GZH_APP_SECRET 时空转等待（骨架态，不刷错误日志）。
- 拉取失败保留上次数据，绝不清表。
"""

import asyncio
import html
import logging
import re
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import delete, select

from app.adapter.database import get_db_ctx
from app.domain.content.src.model.gzh_article import SOURCE_MANUAL, GzhArticle
from app.integrations.gzh import GzhClient, is_configured
from app.port.exceptions import BusinessException

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
            elif row.source != SOURCE_MANUAL:
                # Admin 手动维护的条目不被同步覆盖
                row.title = article["title"]
                row.thumb_url = article["thumb_url"]
                row.publish_time = article["publish_time"]
        await db.commit()
        await _prune_stale(db)
    return len(articles)


async def _prune_stale(db) -> None:
    # 只清理同步来源的未删除行：manual 条目由运营在 Admin 管理，软删行归回收站到期清理
    keep_ids = (
        select(GzhArticle.id)
        .where(
            GzhArticle.source != SOURCE_MANUAL,
            GzhArticle.deleted_at.is_(None),
        )
        .order_by(GzhArticle.publish_time.desc().nullslast(), GzhArticle.id.desc())
        .limit(KEEP_ROWS)
    )
    await db.execute(
        delete(GzhArticle).where(
            GzhArticle.id.not_in(keep_ids),
            GzhArticle.source != SOURCE_MANUAL,
            GzhArticle.deleted_at.is_(None),
        )
    )
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


# ---- 文章页元信息抓取（Admin fetch-meta：批量导入/新增弹窗按链接自动填充）----
# 坑（2026-10-22 回填与 2026-11-13 生产实测）：文章页公开可抓的前提是带浏览器 UA，
# 且链接须为带 chksm 的完整分享链接或 /s/<hash> 短链；公众号后台「复制链接」的裸
# 形态（?__biz=..&mid=..&sn=..）与无 UA 请求均被微信反爬页拦（约 18KB、无 og 标签）。
ARTICLE_PAGE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
# og meta 两种属性顺序都兜住（property 先 / content 先）
_OG_META_RES = (
    '<meta[^>]+property="{prop}"[^>]+content="([^"]*)"',
    '<meta[^>]+content="([^"]*)"[^>]+property="{prop}"',
)
CT_TS_RE = re.compile(r'var\s+ct\s*=\s*"(\d{9,})"')
CREATE_TIME_RE = re.compile(r"var\s+createTime\s*=\s*'(\d{4}-\d{2}-\d{2} \d{2}:\d{2})'")
# 与 GzhArticleCreate/GzhArticleImportItem 的 title 上限保持一致
ARTICLE_TITLE_MAX_LENGTH = 256
BEIJING_TZ = timezone(timedelta(hours=8))
FETCH_META_GUIDE = (
    "微信页面拉取不到文章信息（反爬拦截或链接形态不支持）："
    "请改用微信内打开文章点「···→复制链接」得到的地址，或手动填写标题导入"
)


def _meta_content(page: str, prop: str) -> str | None:
    for pattern in _OG_META_RES:
        m = re.search(pattern.format(prop=prop), page, re.I)
        if m and m.group(1).strip():
            return html.unescape(m.group(1)).strip()
    return None


def parse_article_page_meta(page_html: str) -> dict | None:
    """文章页 HTML → {title, thumb_url, publish_time}；无 og:title 视为反爬页/非文章页，返回 None。"""
    title = _meta_content(page_html, "og:title")
    if not title:
        return None
    thumb = _meta_content(page_html, "og:image")
    publish_time = None
    m = CT_TS_RE.search(page_html)
    if m:
        # var ct = "<unix 秒>"：与 worker 落库约定一致，存 UTC
        publish_time = datetime.fromtimestamp(int(m.group(1)), tz=timezone.utc)
    else:
        m = CREATE_TIME_RE.search(page_html)
        if m:
            # createTime 是北京时间字符串
            publish_time = (
                datetime.strptime(m.group(1), "%Y-%m-%d %H:%M")
                .replace(tzinfo=BEIJING_TZ)
                .astimezone(timezone.utc)
            )
    return {
        "title": title[:ARTICLE_TITLE_MAX_LENGTH],
        "thumb_url": thumb if thumb and thumb.startswith("https://") else None,
        "publish_time": publish_time,
    }


async def fetch_article_page_meta(url: str) -> dict:
    """抓文章页并解析元信息；请求失败或命中反爬页抛 BusinessException（含引导文案）。"""
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0),
            headers={"User-Agent": ARTICLE_PAGE_UA, "Accept-Language": "zh-CN,zh;q=0.9"},
            follow_redirects=True,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            page = resp.text
    except httpx.HTTPError as exc:
        raise BusinessException(f"文章页面请求失败（{type(exc).__name__}），请确认链接可从浏览器打开") from exc
    meta = parse_article_page_meta(page)
    if meta is None:
        raise BusinessException(FETCH_META_GUIDE)
    return meta
