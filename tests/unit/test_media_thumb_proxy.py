"""公众号封面代理端点（GET /api/media/thumb-proxy）。

微信公众号图床带 Referer 反盗链，管理端 `<img>` 直连只拿占位图；
该端点由服务端无 Referer 取原图。域名白名单/协议校验离线可测，
远端拉取用 httpx.MockTransport 仿真。
"""

import httpx
import pytest

from app.api.upload import (
    THUMB_PROXY_MAX_BYTES,
    _fetch_thumb_image,
    proxy_wechat_thumb,
)
from app.port.exceptions import BusinessException, NotFoundException


@pytest.mark.parametrize(
    "url",
    [
        "http://mmbiz.qpic.cn/x.jpg?wx_fmt=jpeg",  # 非 https
        "https://example.com/x.jpg",  # 非微信图床
        "https://mmbiz.qpic.cn.evil.com/x.jpg",  # 仿冒域名（netloc 精确比对）
        "ftp://mmbiz.qpic.cn/x.jpg",
    ],
)
async def test_proxy_rejects_non_wechat_or_insecure_urls(url):
    with pytest.raises(BusinessException):
        await proxy_wechat_thumb(url=url)


def _patch_transport(monkeypatch, handler):
    """让 _fetch_thumb_image 内构造的 AsyncClient 走 MockTransport。"""

    class _FakeClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            kwargs.pop("transport", None)
            super().__init__(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.api.upload.httpx.AsyncClient", _FakeClient)


async def test_proxy_passes_through_image_and_cache_header(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://mmbiz.qpic.cn/img.jpg?wx_fmt=jpeg"
        assert "referer" not in request.headers  # 无 Referer 才拿得到原图
        return httpx.Response(
            200, content=b"\xff\xd8fakejpeg", headers={"content-type": "image/jpeg"}
        )

    _patch_transport(monkeypatch, handler)
    response = await proxy_wechat_thumb(url="https://mmbiz.qpic.cn/img.jpg?wx_fmt=jpeg")
    assert response.body == b"\xff\xd8fakejpeg"
    assert response.media_type == "image/jpeg"
    assert response.headers["cache-control"] == "public, max-age=86400"


async def test_fetch_rejects_non_image_content(monkeypatch):
    _patch_transport(
        monkeypatch,
        lambda request: httpx.Response(200, content=b"<html/>", headers={"content-type": "text/html"}),
    )
    with pytest.raises(NotFoundException):
        await _fetch_thumb_image("https://mmbiz.qpic.cn/page.html")


async def test_fetch_rejects_oversized_content(monkeypatch):
    big = b"x" * (THUMB_PROXY_MAX_BYTES + 1)
    _patch_transport(
        monkeypatch,
        lambda request: httpx.Response(200, content=big, headers={"content-type": "image/jpeg"}),
    )
    with pytest.raises(NotFoundException):
        await _fetch_thumb_image("https://mmbiz.qpic.cn/big.jpg")


async def test_fetch_wraps_http_error_as_not_found(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    _patch_transport(monkeypatch, handler)
    with pytest.raises(NotFoundException):
        await _fetch_thumb_image("https://mmbiz.qpic.cn/gone.jpg")


async def test_fetch_follows_error_status(monkeypatch):
    _patch_transport(monkeypatch, lambda request: httpx.Response(404))
    with pytest.raises(NotFoundException):
        await _fetch_thumb_image("https://mmbiz.qpic.cn/404.jpg")
