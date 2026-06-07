import os

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse

from app.port.exceptions import NotFoundException
from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.schemas.common import APIResponse, success
from app.schemas.upload import UploadResponse
from app.services.upload import UploadService

upload_router = APIRouter(prefix="/upload", tags=["文件"])
media_router = APIRouter(prefix="/media", tags=["文件"])


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