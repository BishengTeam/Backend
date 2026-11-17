"""公众号资讯 schema 封面校验：https 外链或本地上传的 /api/media/ 站内地址均可。

背景（2026-11-17 修复）：Admin ImageUpload 上传返回 /api/media/<file> 相对地址，
旧的「封面必须 https://」校验导致上传后保存被 422 拦下（前端表现为点确定无反应）。
"""

import pytest
from pydantic import ValidationError

from app.schemas.admin_gzh_article import (
    GzhArticleCreate,
    GzhArticleImportItem,
    GzhArticleUpdate,
)


def _create_kwargs(thumb_url: str | None) -> dict:
    return {
        "title": "标题",
        "url": "https://mp.weixin.qq.com/s/abc",
        "thumb_url": thumb_url,
    }


@pytest.mark.parametrize(
    "thumb",
    [
        "https://mmbiz.qpic.cn/x.jpg?wx_fmt=jpeg",
        "/api/media/abc123.jpg",
    ],
)
def test_create_accepts_https_and_local_media_thumb(thumb):
    item = GzhArticleCreate(**_create_kwargs(thumb))
    assert item.thumb_url == thumb


@pytest.mark.parametrize(
    "bad",
    [
        "http://mmbiz.qpic.cn/x.jpg",
        "mmbiz.qpic.cn/x.jpg",
        "/uploads/x.jpg",
        "ftp://example.com/x.jpg",
    ],
)
def test_create_rejects_other_thumb_forms(bad):
    with pytest.raises(ValidationError):
        GzhArticleCreate(**_create_kwargs(bad))


def test_create_still_requires_https_url():
    with pytest.raises(ValidationError):
        GzhArticleCreate(title="标题", url="http://mp.weixin.qq.com/s/abc")


def test_null_thumb_still_allowed():
    item = GzhArticleCreate(**_create_kwargs(None))
    assert item.thumb_url is None


def test_update_and_import_accept_local_media_thumb():
    upd = GzhArticleUpdate(thumb_url="/api/media/abc.jpg")
    assert upd.thumb_url == "/api/media/abc.jpg"
    item = GzhArticleImportItem(
        title="标题",
        url="https://mp.weixin.qq.com/s/abc",
        thumb_url="/api/media/abc.jpg",
    )
    assert item.thumb_url == "/api/media/abc.jpg"
