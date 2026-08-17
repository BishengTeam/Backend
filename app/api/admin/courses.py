from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, Path, Query, UploadFile
from typing import Any

from app.middleware.auth import require_super_admin
from app.port.exceptions import BusinessException
from app.schemas.admin_course import (
    AdminCourseAssetResponse,
    AdminCourseAuditListItem,
    AdminCourseBindingCreate,
    AdminCourseBindingImpact,
    AdminCourseBindingResponse,
    AdminCourseBindingStatusRequest,
    AdminCourseCategoryCreate,
    AdminCourseCategoryResponse,
    AdminCourseCategoryUpdate,
    AdminCourseCreate,
    AdminCourseEntitlementJobResponse,
    AdminCourseEnrollmentListItem,
    AdminCourseListItem,
    AdminCourseLifecycleRequest,
    AdminCourseUpdate,
)
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.course import (
    ChapterCreate,
    ChapterResponse,
    ChapterSortItem,
    ChapterUpdate,
    EnrollmentStatus,
)
from app.services.admin_course import AdminCourseService
from app.services.course_entitlement import CourseEntitlementService


router = APIRouter(prefix="/courses", tags=["管理后台-课程管理"])


@router.get(
    "",
    response_model=APIResponse[PaginatedData[AdminCourseListItem]],
    summary="课程列表",
)
async def list_courses(
    keyword: str | None = Query(None),
    category: str | None = Query(None),
    status: str | None = Query(None, pattern="^(draft|published|offline|archived)$"),
    price_type: str | None = Query(None, pattern="^(free|paid)$"),
    bound_quiz: bool | None = Query(None),
    created_from: datetime | None = Query(None),
    created_to: datetime | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(require_super_admin),
) -> APIResponse[PaginatedData[AdminCourseListItem]]:
    result = await AdminCourseService().list_courses(
        keyword=keyword,
        category=category,
        status=status,
        price_type=price_type,
        bound_quiz=bound_quiz,
        created_from=created_from,
        created_to=created_to,
        page=page,
        page_size=page_size,
    )
    return success(data=result)


@router.post(
    "",
    response_model=APIResponse[AdminCourseListItem],
    summary="创建课程",
)
async def create_course(
    body: AdminCourseCreate,
    admin=Depends(require_super_admin),
) -> APIResponse[AdminCourseListItem]:
    result = await AdminCourseService().create_course(body, admin_id=admin.id)
    return success(data=result, message="课程已创建")


@router.get(
    "/categories",
    response_model=APIResponse[list[AdminCourseCategoryResponse]],
    summary="课程类目列表",
)
async def list_categories(
    _admin=Depends(require_super_admin),
) -> APIResponse[list[AdminCourseCategoryResponse]]:
    return success(data=await AdminCourseService().list_categories())


@router.post(
    "/categories",
    response_model=APIResponse[AdminCourseCategoryResponse],
    summary="创建课程类目",
)
async def create_category(
    body: AdminCourseCategoryCreate,
    admin=Depends(require_super_admin),
) -> APIResponse[AdminCourseCategoryResponse]:
    result = await AdminCourseService().create_category(body, admin_id=admin.id)
    return success(data=result, message="课程类目已创建")


@router.put(
    "/categories/{category_id}",
    response_model=APIResponse[AdminCourseCategoryResponse],
    summary="编辑课程类目",
)
async def update_category(
    category_id: int = Path(..., ge=1),
    body: AdminCourseCategoryUpdate = ...,
    admin=Depends(require_super_admin),
) -> APIResponse[AdminCourseCategoryResponse]:
    result = await AdminCourseService().update_category(
        category_id, body, admin_id=admin.id
    )
    return success(data=result, message="课程类目已更新")


@router.get(
    "/audit-logs",
    response_model=APIResponse[PaginatedData[AdminCourseAuditListItem]],
    summary="课程审计",
)
async def list_audit_logs(
    course_id: int | None = Query(None, ge=1),
    action: str | None = Query(None),
    result: str | None = Query(None, pattern="^(succeeded|failed)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(require_super_admin),
) -> APIResponse[PaginatedData[AdminCourseAuditListItem]]:
    return success(
        data=await AdminCourseService().list_audit_logs(
            course_id=course_id,
            action=action,
            result=result,
            page=page,
            page_size=page_size,
        )
    )


@router.get(
    "/enrollments",
    response_model=APIResponse[PaginatedData[AdminCourseEnrollmentListItem]],
    summary="课程报名学员",
)
async def list_enrollments(
    course_id: int | None = Query(None, ge=1),
    user_id: int | None = Query(None, ge=1),
    status: EnrollmentStatus | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(require_super_admin),
) -> APIResponse[PaginatedData[AdminCourseEnrollmentListItem]]:
    return success(
        data=await AdminCourseService().list_enrollments(
            course_id=course_id,
            user_id=user_id,
            status=status,
            page=page,
            page_size=page_size,
        )
    )


@router.get(
    "/{course_id}",
    response_model=APIResponse[AdminCourseListItem],
    summary="课程详情",
)
async def get_course(
    course_id: int = Path(..., ge=1),
    _admin=Depends(require_super_admin),
) -> APIResponse[AdminCourseListItem]:
    return success(data=await AdminCourseService().get_course(course_id))


@router.put(
    "/{course_id}",
    response_model=APIResponse[AdminCourseListItem],
    summary="编辑课程",
)
async def update_course(
    body: AdminCourseUpdate,
    course_id: int = Path(..., ge=1),
    admin=Depends(require_super_admin),
) -> APIResponse[AdminCourseListItem]:
    result = await AdminCourseService().update_course(
        course_id, body, admin_id=admin.id
    )
    return success(data=result, message="课程已更新")


@router.post(
    "/{course_id}/lifecycle",
    response_model=APIResponse[AdminCourseListItem],
    summary="课程生命周期操作",
)
async def change_course_lifecycle(
    body: AdminCourseLifecycleRequest,
    course_id: int = Path(..., ge=1),
    admin=Depends(require_super_admin),
) -> APIResponse[AdminCourseListItem]:
    result = await AdminCourseService().change_lifecycle(
        course_id, body.action, admin_id=admin.id
    )
    messages = {
        "publish": "课程已发布",
        "offline": "课程已下线",
        "archive": "课程已归档",
        "restore": "课程已恢复发布",
    }
    return success(data=result, message=messages[body.action])


@router.delete(
    "/{course_id}",
    response_model=APIResponse,
    summary="删除草稿课程",
)
async def delete_course(
    course_id: int = Path(..., ge=1),
    admin=Depends(require_super_admin),
) -> APIResponse:
    await AdminCourseService().delete_course(course_id, admin_id=admin.id)
    return success(message="课程已删除")


@router.get(
    "/{course_id}/chapters",
    response_model=APIResponse[PaginatedData[ChapterResponse]],
    summary="章节列表",
)
async def list_chapters(
    course_id: int = Path(..., ge=1),
    is_active: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(require_super_admin),
) -> APIResponse[PaginatedData[ChapterResponse]]:
    return success(
        data=await AdminCourseService().list_chapters(
            course_id, is_active, page, page_size
        )
    )


@router.post(
    "/{course_id}/chapters",
    response_model=APIResponse[ChapterResponse],
    summary="新增章节",
)
async def create_chapter(
    body: ChapterCreate,
    course_id: int = Path(..., ge=1),
    admin=Depends(require_super_admin),
) -> APIResponse[ChapterResponse]:
    return success(
        data=await AdminCourseService().create_chapter(
            course_id, body, admin_id=admin.id
        )
    )


@router.put(
    "/{course_id}/chapters/sort",
    response_model=APIResponse[int],
    summary="章节排序",
)
async def sort_chapters(
    body: list[ChapterSortItem],
    course_id: int = Path(..., ge=1),
    admin=Depends(require_super_admin),
) -> APIResponse[int]:
    count = await AdminCourseService().sort_chapters(
        course_id, body, admin_id=admin.id
    )
    return success(data=count, message=f"已更新 {count} 个章节排序")


@router.put(
    "/{course_id}/chapters/{chapter_id}",
    response_model=APIResponse[ChapterResponse],
    summary="编辑章节",
)
async def update_chapter(
    body: ChapterUpdate,
    course_id: int = Path(..., ge=1),
    chapter_id: int = Path(..., ge=1),
    admin=Depends(require_super_admin),
) -> APIResponse[ChapterResponse]:
    return success(
        data=await AdminCourseService().update_chapter(
            chapter_id, body, admin_id=admin.id
        )
    )


@router.delete(
    "/{course_id}/chapters/{chapter_id}",
    response_model=APIResponse,
    summary="下架章节",
)
async def delete_chapter(
    course_id: int = Path(..., ge=1),
    chapter_id: int = Path(..., ge=1),
    admin=Depends(require_super_admin),
) -> APIResponse:
    await AdminCourseService().delete_chapter(chapter_id, admin_id=admin.id)
    return success(message="章节已下架")


@router.get(
    "/{course_id}/assets",
    response_model=APIResponse[list[AdminCourseAssetResponse]],
    summary="课程资料列表",
)
async def list_assets(
    course_id: int = Path(..., ge=1),
    _admin=Depends(require_super_admin),
) -> APIResponse[list[AdminCourseAssetResponse]]:
    return success(data=await AdminCourseService().list_assets(course_id))


@router.post(
    "/{course_id}/assets",
    response_model=APIResponse[AdminCourseAssetResponse],
    summary="上传课程资料",
)
async def create_asset(
    course_id: int = Path(..., ge=1),
    file: UploadFile = File(...),
    title: str = Form(..., min_length=1, max_length=256),
    asset_type: str = Form(..., min_length=1, max_length=32),
    sort_order: int = Form(0, ge=0),
    is_preview: bool = Form(False),
    admin=Depends(require_super_admin),
) -> APIResponse[AdminCourseAssetResponse]:
    content = await file.read()
    result = await AdminCourseService().create_asset(
        course_id,
        filename=file.filename or "asset",
        content=content,
        title=title,
        asset_type=asset_type,
        sort_order=sort_order,
        is_preview=is_preview,
        admin_id=admin.id,
    )
    return success(data=result, message="课程资料已上传")


@router.delete(
    "/{course_id}/assets/{asset_id}",
    response_model=APIResponse,
    summary="删除课程资料",
)
async def delete_asset(
    course_id: int = Path(..., ge=1),
    asset_id: int = Path(..., ge=1),
    admin=Depends(require_super_admin),
) -> APIResponse:
    await AdminCourseService().delete_asset(
        course_id, asset_id, admin_id=admin.id
    )
    return success(message="课程资料已删除")


@router.get(
    "/{course_id}/quiz-bindings",
    response_model=APIResponse[list[AdminCourseBindingResponse]],
    summary="课程赠送题库列表",
)
async def list_bindings(
    course_id: int = Path(..., ge=1),
    _admin=Depends(require_super_admin),
) -> APIResponse[list[AdminCourseBindingResponse]]:
    return success(data=await AdminCourseService().list_bindings(course_id))


@router.get(
    "/{course_id}/quiz-bindings/impact",
    response_model=APIResponse[AdminCourseBindingImpact],
    summary="课程题库绑定影响预览",
)
async def preview_binding(
    course_id: int = Path(..., ge=1),
    library_id: int = Query(..., ge=1),
    _admin=Depends(require_super_admin),
) -> APIResponse[AdminCourseBindingImpact]:
    return success(
        data=await CourseEntitlementService.preview_binding(course_id, library_id)
    )


@router.post(
    "/{course_id}/quiz-bindings",
    response_model=APIResponse[AdminCourseEntitlementJobResponse],
    summary="新增课程题库绑定并回补",
)
async def create_binding(
    body: AdminCourseBindingCreate,
    course_id: int = Path(..., ge=1),
    admin=Depends(require_super_admin),
) -> APIResponse[AdminCourseEntitlementJobResponse]:
    if body.backfill_confirmations != ["impact_confirmed"]:
        raise BusinessException("请先确认绑定影响并执行回补")
    job = await CourseEntitlementService.create_binding(
        course_id, body.library_id, admin_id=admin.id
    )
    return success(data=job, message="绑定已创建，回补任务已排队")


@router.post(
    "/course-quiz-bindings/{binding_id}/status",
    response_model=APIResponse[AdminCourseEntitlementJobResponse],
    summary="启用或停用课程题库绑定",
)
async def set_binding_status(
    binding_id: int = Path(..., ge=1),
    body: AdminCourseBindingStatusRequest = ...,
    admin=Depends(require_super_admin),
) -> APIResponse[AdminCourseEntitlementJobResponse]:
    job = await CourseEntitlementService.set_binding_status(
        binding_id, body.status, admin_id=admin.id
    )
    return success(data=job, message="绑定状态已更新，权益任务已排队")


@router.get(
    "/{course_id}/entitlement-jobs",
    response_model=APIResponse[PaginatedData[AdminCourseEntitlementJobResponse]],
    summary="课程权益任务",
)
async def list_jobs(
    course_id: int = Path(..., ge=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(require_super_admin),
) -> APIResponse[PaginatedData[AdminCourseEntitlementJobResponse]]:
    return success(
        data=await AdminCourseService().list_jobs(
            course_id, page=page, page_size=page_size
        )
    )


@router.post(
    "/course-entitlement-jobs/{job_id}/retry",
    response_model=APIResponse[AdminCourseEntitlementJobResponse],
    summary="重试课程权益任务",
)
async def retry_job(
    job_id: int = Path(..., ge=1),
    admin=Depends(require_super_admin),
) -> APIResponse[AdminCourseEntitlementJobResponse]:
    return success(
        data=await AdminCourseService().retry_job(job_id, admin_id=admin.id),
        message="失败项已重新排队",
    )
