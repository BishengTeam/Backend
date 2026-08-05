from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import FileResponse

from app.domain.user.src.index import User
from app.middleware.auth import get_current_user, get_current_user_optional
from app.port.exceptions import UnauthorizedException
from app.schemas.common import APIResponse, success
from app.schemas.course import CourseAssetPlaybackResponse
from app.services.course_asset import CourseAssetService


router = APIRouter(prefix="/course-assets", tags=["课程"])


@router.post(
    "/{asset_id}/playback-url",
    response_model=APIResponse[CourseAssetPlaybackResponse],
    summary="签发课程资源播放地址",
    description="校验当前用户学习权限后，签发绑定用户和资源、有效期两小时的播放地址。",
)
async def create_course_asset_playback_url(
    asset_id: int = Path(..., ge=1, description="课程资源 ID"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[CourseAssetPlaybackResponse]:
    url, expires_at = await CourseAssetService().issue_playback_url(
        current_user.id,
        asset_id,
    )
    return success(
        data=CourseAssetPlaybackResponse(
            asset_id=asset_id,
            url=url,
            expires_at=expires_at,
        )
    )


@router.get(
    "/{asset_id}/content",
    response_class=FileResponse,
    summary="访问课程资源",
    description="试看资源或已开通学习权限的私有课程资源。",
)
async def get_course_asset_content(
    asset_id: int = Path(..., ge=1, description="课程资源 ID"),
    user_id: int | None = Query(None, ge=1),
    expires: int | None = Query(None, ge=1),
    signature: str | None = Query(None, min_length=64, max_length=64),
    current_user: User | None = Depends(get_current_user_optional),
):
    service = CourseAssetService()
    if current_user is not None:
        resolved_user_id = current_user.id
    elif user_id is not None and expires is not None and signature is not None:
        service.verify_playback_signature(
            user_id,
            asset_id,
            expires,
            signature,
        )
        resolved_user_id = user_id
    else:
        raise UnauthorizedException("请先登录或使用有效播放地址")

    # 即使签名仍有效，也实时检查当前学习权限，确保撤权立即生效。
    asset_file = await service.get_content(resolved_user_id, asset_id)
    return FileResponse(
        path=str(asset_file.path),
        filename=asset_file.filename,
        media_type=asset_file.media_type,
        content_disposition_type="inline" if asset_file.inline else "attachment",
    )
