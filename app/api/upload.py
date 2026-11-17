import os
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse, Response

from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.port.exceptions import BusinessException, NotFoundException
from app.schemas.common import APIResponse, success
from app.schemas.upload import UploadResponse
from app.services.upload import UploadService

upload_router = APIRouter(prefix="/upload", tags=["系统"])
media_router = APIRouter(prefix="/media", tags=["系统"])

# 微信公众平台图片带 Referer 反盗链：浏览器从管理端页面直连 mmbiz 图床只会拿到
# 「未经允许不可引用」占位图，服务端无 Referer 可取原图。
# 仅代理微信图床域名 + https，防止该接口被当通用图片代理用。
THUMB_PROXY_HOSTS = frozenset({"mmbiz.qpic.cn", "mmbiz.qlogo.cn"})
THUMB_PROXY_MAX_BYTES = 10 * 1024 * 1024
_THUMB_PROXY_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


@media_router.get("/thumb-proxy",
    summary="代理访问微信公众平台图片",
    description="""
管理后台 **公众号资讯封面** 展示使用。

微信公众平台图片（mmbiz.qpic.cn）带 Referer 反盗链，管理端页面 `<img>` 直连只拿到
占位图；本接口由服务端（无 Referer）拉取原图后回传。仅接受
`https://mmbiz.qpic.cn/...` 与 `https://mmbiz.qlogo.cn/...`，带 1 天浏览器缓存。

**认证**: 无需登录（`<img>` 标签无法携带鉴权头；域名白名单防滥用）
    """,
)
async def proxy_wechat_thumb(
    url: str = Query(..., description="微信图床图片地址（https://mmbiz.qpic.cn/...）"),
):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() not in THUMB_PROXY_HOSTS:
        raise BusinessException("仅支持微信公众平台图片地址（https://mmbiz.qpic.cn/...）")
    data, content_type = await _fetch_thumb_image(url)
    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


async def _fetch_thumb_image(url: str) -> tuple[bytes, str]:
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0),
            headers={"User-Agent": _THUMB_PROXY_UA},  # 不带 Referer 是绕开反盗链的关键
            follow_redirects=True,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise NotFoundException("图片") from exc
    content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    if not content_type.startswith("image/") or len(resp.content) > THUMB_PROXY_MAX_BYTES:
        raise NotFoundException("图片")
    return resp.content, content_type


@upload_router.post("",
    response_model=APIResponse[UploadResponse],
    summary="上传文件",
    description="""
小程序 **文件上传** 功能使用。

**使用场景**: 用户上传图片、文档等文件

**请求体**: multipart/form-data，字段 `file`

**响应**: 文件 ID 和访问 URL

**认证**: 需登录
    """,
)
async def upload(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> APIResponse[UploadResponse]:
    """上传文件 — 需要登录"""
    result = await UploadService.save_file(file)
    return success(data=UploadResponse(**result))


@media_router.get("/{file_id}",
    response_class=FileResponse,
    summary="访问文件",
    description="""
文件访问接口。

**使用场景**: 通过文件 ID 直接访问或下载文件

**路径参数**:
- `file_id`: 文件 ID（含扩展名）

**响应**: 文件二进制流，自动推断 MIME 类型

**认证**: 无需登录
    """,
)
async def get_file(file_id: str):
    """访问/下载文件 — 无需登录"""
    if not UploadService.file_exists(file_id):
        raise NotFoundException("文件")

    file_path = UploadService.get_file_path(file_id)
    # 从扩展名推断 media_type
    _, ext = os.path.splitext(file_id)
    return FileResponse(str(file_path), filename=file_id, media_type=_guess_media_type(ext))


def _guess_media_type(ext: str) -> str | None:
    """根据扩展名返回 MIME 类型（常见图片/文档类型）"""
    mapping = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
        ".pdf": "application/pdf",
        ".mp4": "video/mp4",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".zip": "application/zip",
    }
    return mapping.get(ext.lower())
