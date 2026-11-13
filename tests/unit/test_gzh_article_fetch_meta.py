"""Unit tests for 公众号文章页元信息抓取（Admin fetch-meta）。

解析器纯离线；fixture 依据 2026-11-13 生产机实测的真实页面字段
（og:title / og:image / var ct 时间戳 / var createTime 北京时间）。
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.port.exceptions import BusinessException
from app.services.admin_gzh_article import AdminGzhArticleService
from app.services.gzh_article import (
    ARTICLE_TITLE_MAX_LENGTH,
    parse_article_page_meta,
)

BEIJING_TZ = timezone(timedelta(hours=8))


def _page(
    *,
    title: str | None = "产教喜报｜我司张虎入选&quot;首批&quot;名单",
    image: str | None = "https://mmbiz.qpic.cn/mmbiz_jpg/abc/0?wx_fmt=jpeg",
    ct: str | None = "1792461403",
    create_time: str | None = None,
    attr_order: str = "prop_first",
) -> str:
    def meta(prop: str, value: str | None) -> str:
        if value is None:
            return ""
        if attr_order == "content_first":
            return f'<meta content="{value}" property="{prop}" />'
        return f'<meta property="{prop}" content="{value}" />'

    ct_line = f'var ct = "{ct}";' if ct is not None else ""
    create_line = f"var createTime = '{create_time}';" if create_time is not None else ""
    return (
        "<!DOCTYPE html><html><head>"
        + meta("og:title", title)
        + meta("og:image", image)
        + "</head><body><script>var msgList='';"
        + ct_line
        + create_line
        + "</script></body></html>"
    )


def test_parse_full_meta_with_ct_timestamp():
    meta = parse_article_page_meta(_page())
    assert meta is not None
    # og:title 的 HTML 实体需反转义
    assert meta["title"] == '产教喜报｜我司张虎入选"首批"名单'
    assert meta["thumb_url"] == "https://mmbiz.qpic.cn/mmbiz_jpg/abc/0?wx_fmt=jpeg"
    # 与 worker 同约定：UTC
    expected = datetime.fromtimestamp(1792461403, tz=timezone.utc)
    assert meta["publish_time"] == expected
    # 实测锚点：该时间戳的北京时间是 2026-10-20 09:56（createTime 与之同源）
    assert expected.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M") == "2026-10-20 09:56"


def test_parse_create_time_fallback_beijing_to_utc():
    meta = parse_article_page_meta(_page(ct=None, create_time="2026-10-20 09:56"))
    assert meta is not None
    assert meta["publish_time"] == datetime(2026, 10, 20, 1, 56, tzinfo=timezone.utc)


def test_parse_blocked_page_returns_none():
    # 微信反爬页：小页面、无 og 标签
    blocked = "<html><head><title>未知错误</title></head><body>环境异常，完成验证后即可继续访问。</body></html>"
    assert parse_article_page_meta(blocked) is None
    assert parse_article_page_meta("") is None


def test_parse_attr_order_variant():
    meta = parse_article_page_meta(_page(attr_order="content_first"))
    assert meta is not None
    assert meta["title"].startswith("产教喜报")
    assert meta["thumb_url"] == "https://mmbiz.qpic.cn/mmbiz_jpg/abc/0?wx_fmt=jpeg"


def test_parse_http_thumb_dropped():
    meta = parse_article_page_meta(_page(image="http://mmbiz.qpic.cn/x.jpg"))
    assert meta is not None
    assert meta["thumb_url"] is None


def test_parse_no_image_no_time():
    meta = parse_article_page_meta(_page(image=None, ct=None, create_time=None))
    assert meta is not None
    assert meta["thumb_url"] is None
    assert meta["publish_time"] is None
    assert meta["title"].startswith("产教喜报")


def test_parse_title_truncated():
    long_title = "标" * (ARTICLE_TITLE_MAX_LENGTH + 50)
    meta = parse_article_page_meta(_page(title=long_title))
    assert meta is not None
    assert len(meta["title"]) == ARTICLE_TITLE_MAX_LENGTH


async def test_fetch_meta_rejects_non_wechat_url():
    # 域名/协议校验在查库与抓取之前，不触 DB / 网络
    with pytest.raises(BusinessException):
        await AdminGzhArticleService().fetch_meta("https://example.com/article")
    with pytest.raises(BusinessException):
        await AdminGzhArticleService().fetch_meta("http://mp.weixin.qq.com/s/abc")
