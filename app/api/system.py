from fastapi import APIRouter, Depends, Path, UploadFile, File

from app.port.config import settings
from app.middleware.auth import get_current_user
from app.models.user import User
from app.schemas.common import APIResponse, success
from app.schemas.system import PosterResponse

router = APIRouter(prefix="/system", tags=["系统"])


@router.get("/poster", response_model=APIResponse[PosterResponse])
async def get_login_poster(
    current_user: User = Depends(get_current_user),
) -> APIResponse[PosterResponse]:
    return success(data=PosterResponse(url=settings.LOGIN_POSTER_URL))


@router.post("/upload", response_model=APIResponse[dict])
async def upload_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> APIResponse[dict]:
    """文件上传 OSS（mock 实现）"""
    filename = file.filename or "unknown"
    return success(data={
        "url": f"https://oss.example.com/uploads/{filename}",
        "key": f"uploads/{filename}",
    })


@router.get("/media/{media_id}", response_model=APIResponse[dict])
async def get_media_url(
    media_id: int = Path(..., description="媒体 ID"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[dict]:
    """文件访问 URL（mock 实现）"""
    return success(data={"url": f"https://oss.example.com/files/{media_id}"})
