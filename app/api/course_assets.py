from fastapi import APIRouter, Depends, Path
from fastapi.responses import FileResponse

from app.domain.user.src.index import User
from app.middleware.auth import get_current_user
from app.services.course_asset import CourseAssetService


router = APIRouter(prefix="/course-assets", tags=["课程"])


@router.get(
    "/{asset_id}/content",
    response_class=FileResponse,
    summary="访问课程资源",
    description="试看资源或已开通学习权限的私有课程资源。",
)
async def get_course_asset_content(
    asset_id: int = Path(..., ge=1, description="课程资源 ID"),
    current_user: User = Depends(get_current_user),
):
    asset_file = await CourseAssetService().get_content(current_user.id, asset_id)
    return FileResponse(
        path=str(asset_file.path),
        filename=asset_file.filename,
        media_type=asset_file.media_type,
        content_disposition_type="inline" if asset_file.inline else "attachment",
    )
