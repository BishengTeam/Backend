from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import require_permission
from app.schemas.common import APIResponse, success
from app.schemas.course_upload import (
    CourseUploadCompleteResponse,
    CourseUploadCreate,
    CourseUploadPartRequest,
    CourseUploadPartUrlResponse,
    CourseUploadResponse,
)
from app.services.course_upload import CourseUploadService


router = APIRouter(prefix="/course-uploads", tags=["管理后台-课程上传"])


@router.post("", response_model=APIResponse[CourseUploadResponse])
async def create_upload(
    body: CourseUploadCreate,
    admin=Depends(require_permission("course:write")),
):
    return success(
        data=await CourseUploadService().create(body, admin_id=admin.id),
        message="上传任务已创建",
    )


@router.get("", response_model=APIResponse[list[CourseUploadResponse]])
async def list_uploads(
    course_id: int = Query(..., ge=1),
    _admin=Depends(require_permission("course:read")),
):
    return success(data=await CourseUploadService().list_course_uploads(course_id))


@router.get("/{upload_id}", response_model=APIResponse[CourseUploadResponse])
async def get_upload(
    upload_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("course:read")),
):
    return success(data=await CourseUploadService().detail(upload_id))


@router.post(
    "/{upload_id}/parts",
    response_model=APIResponse[CourseUploadPartUrlResponse],
)
async def create_part_url(
    upload_id: int = Path(..., ge=1),
    body: CourseUploadPartRequest = ...,
    _admin=Depends(require_permission("course:write")),
):
    return success(
        data=await CourseUploadService().part_url(
            upload_id, body.part_number
        )
    )


@router.post(
    "/{upload_id}/complete",
    response_model=APIResponse[CourseUploadCompleteResponse],
)
async def complete_upload(
    upload_id: int = Path(..., ge=1),
    admin=Depends(require_permission("course:write")),
):
    upload = await CourseUploadService().complete(upload_id, admin_id=admin.id)
    return success(
        data=CourseUploadCompleteResponse(upload=upload, object_key=upload.object_key),
        message="文件已上传完成",
    )


@router.post("/{upload_id}/abort", response_model=APIResponse)
async def abort_upload(
    upload_id: int = Path(..., ge=1),
    admin=Depends(require_permission("course:write")),
):
    await CourseUploadService().abort(upload_id, admin_id=admin.id)
    return success(message="上传任务已放弃")
