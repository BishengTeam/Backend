from fastapi import APIRouter, Depends, Path, UploadFile, File

from app.port.config import settings
from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.schemas.common import APIResponse, success
from app.schemas.system import PosterResponse

router = APIRouter(prefix="/system", tags=["系统"])


@router.get("/poster",
    response_model=APIResponse[PosterResponse],
    summary="获取登录页海报",
    description="""
小程序 **登录页** 使用。

**使用场景**: 登录页加载时获取海报图片，用于登录页背景展示

**响应**: 海报图片 URL

**认证**: 需登录
    """,
)
async def get_login_poster(
    current_user: User = Depends(get_current_user),
) -> APIResponse[PosterResponse]:
    return success(data=PosterResponse(url=settings.LOGIN_POSTER_URL))


@router.post("/upload",
    response_model=APIResponse[dict],
    summary="文件上传",
    description="""
系统文件上传接口。

**使用场景**: 上传文件到 OSS，返回文件访问 URL 和 key

**请求体**: multipart/form-data，字段 `file`

**响应**: 含 `url` 和 `key` 的字典

**认证**: 需登录
    """,
)
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


@router.get("/media/{media_id}",
    response_model=APIResponse[dict],
    summary="获取媒体文件",
    description="""
系统媒体文件访问接口。

**使用场景**: 根据媒体 ID 获取文件访问 URL

**路径参数**:
- `media_id`: 媒体文件 ID

**响应**: 含文件 URL 的字典

**认证**: 需登录
    """,
)
async def get_media_url(
    media_id: int = Path(..., description="媒体 ID"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[dict]:
    """文件访问 URL（mock 实现）"""
    return success(data={"url": f"https://oss.example.com/files/{media_id}"})