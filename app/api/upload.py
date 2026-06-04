import os

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse

from app.port.exceptions import NotFoundException
from app.middleware.auth import get_current_user
from app.models.user import User
from app.schemas.common import APIResponse, success
from app.schemas.upload import UploadResponse
from app.services.upload import UploadService

upload_router = APIRouter(prefix="/upload", tags=["文件"])
media_router = APIRouter(prefix="/media", tags=["文件"])


@upload_router.post("", response_model=APIResponse[UploadResponse])
async def upload(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> APIResponse[UploadResponse]:
    """上传文件 — 需要登录"""
    result = await UploadService.save_file(file)
    return success(data=UploadResponse(**result))


@media_router.get("/{file_id}")
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
