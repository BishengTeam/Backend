from fastapi import APIRouter, Body, Depends, Path, Query

from app.middleware.auth import require_permission, require_super_admin
from app.port.exceptions import BusinessException
from app.schemas.admin_course import (
    AdminChapterBatchCreate,
    AdminChapterResponse,
    AdminChapterReplaceVideo,
    AdminChapterSortItem,
    AdminChapterUpdate,
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
    AdminCourseAuditListItem,
    AdminCourseListItem,
    AdminCourseLifecycleRequest,
    AdminCoursePriceUpdate,
    AdminCourseUpdate,
)
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.course import CourseStatus, EnrollmentStatus
from app.services.admin_course import AdminCourseService
from app.services.course_entitlement import CourseEntitlementService


router = APIRouter(prefix="/courses", tags=["管理后台-课程管理"])


@router.get("", response_model=APIResponse[PaginatedData[AdminCourseListItem]])
async def list_courses(
    keyword: str | None = Query(None),
    category: str | None = Query(None),
    status: CourseStatus | None = Query(None),
    price_type: str | None = Query(None, pattern="^(free|paid)$"),
    bound_quiz: bool | None = Query(None),
    created_from: str | None = Query(None),
    created_to: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(require_permission("course:read")),
):
    from datetime import datetime

    result = await AdminCourseService().list_courses(
        keyword=keyword,
        category=category,
        status=status,
        price_type=price_type,
        bound_quiz=bound_quiz,
        created_from=datetime.fromisoformat(created_from) if created_from else None,
        created_to=datetime.fromisoformat(created_to) if created_to else None,
        page=page,
        page_size=page_size,
    )
    return success(data=result)


@router.post("", response_model=APIResponse[AdminCourseListItem])
async def create_course(
    body: AdminCourseCreate,
    _admin=Depends(require_permission("course:write")),
):
    return success(
        data=await AdminCourseService().create_course(body, admin_id=_admin.id),
        message="课程草稿已创建",
    )


@router.get("/categories", response_model=APIResponse[list[AdminCourseCategoryResponse]])
async def list_categories(
    _admin=Depends(require_permission("course:read")),
):
    return success(data=await AdminCourseService().list_categories())


@router.post("/categories", response_model=APIResponse[AdminCourseCategoryResponse])
async def create_category(
    body: AdminCourseCategoryCreate,
    admin=Depends(require_permission("course:write")),
):
    return success(
        data=await AdminCourseService().create_category(body, admin_id=admin.id),
        message="课程类目已创建",
    )


@router.put("/categories/{category_id}", response_model=APIResponse[AdminCourseCategoryResponse])
async def update_category(
    category_id: int = Path(..., ge=1),
    body: AdminCourseCategoryUpdate = ...,
    admin=Depends(require_permission("course:write")),
):
    return success(
        data=await AdminCourseService().update_category(
            category_id, body, admin_id=admin.id
        )
    )


@router.get("/enrollments", response_model=APIResponse[PaginatedData[AdminCourseEnrollmentListItem]])
async def list_enrollments(
    course_id: int | None = Query(None, ge=1),
    user_id: int | None = Query(None, ge=1),
    status: EnrollmentStatus | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(require_permission("course:read")),
):
    return success(
        data=await AdminCourseService().list_enrollments(
            course_id=course_id,
            user_id=user_id,
            status=status,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/audit-logs", response_model=APIResponse[PaginatedData[AdminCourseAuditListItem]])
async def list_audit_logs(
    course_id: int | None = Query(None, ge=1),
    action: str | None = Query(None, max_length=64),
    result: str | None = Query(None, pattern="^(succeeded|failed)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(require_permission("course:read")),
):
    return success(
        data=await AdminCourseService().list_audit_logs(
            course_id=course_id,
            action=action,
            result=result,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/{course_id}", response_model=APIResponse[AdminCourseListItem])
async def get_course(
    course_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("course:read")),
):
    return success(data=await AdminCourseService().get_course(course_id))


@router.put("/{course_id}", response_model=APIResponse[AdminCourseListItem])
async def update_course(
    course_id: int = Path(..., ge=1),
    body: AdminCourseUpdate = ...,
    admin=Depends(require_permission("course:write")),
):
    return success(
        data=await AdminCourseService().update_course(
            course_id, body, admin_id=admin.id
        )
    )


@router.put("/{course_id}/price", response_model=APIResponse[AdminCourseListItem])
async def update_price(
    course_id: int = Path(..., ge=1),
    body: AdminCoursePriceUpdate = ...,
    admin=Depends(require_super_admin),
):
    return success(
        data=await AdminCourseService().update_price(
            course_id, body.price_yuan, admin_id=admin.id
        )
    )


@router.post("/{course_id}/lifecycle", response_model=APIResponse[AdminCourseListItem])
async def change_lifecycle(
    course_id: int = Path(..., ge=1),
    body: AdminCourseLifecycleRequest = ...,
    admin=Depends(require_permission("course:publish")),
):
    if body.action in {"archive", "restore"} and admin.role != "super_admin":
        raise BusinessException("归档和恢复课程需要超级管理员")
    return success(
        data=await AdminCourseService().change_lifecycle(
            course_id, body.action, admin_id=admin.id
        )
    )


@router.delete("/{course_id}", response_model=APIResponse)
async def delete_course(
    course_id: int = Path(..., ge=1),
    admin=Depends(require_super_admin),
):
    await AdminCourseService().delete_course(course_id, admin_id=admin.id)
    return success(message="课程已删除")


@router.get(
    "/{course_id}/chapters",
    response_model=APIResponse[PaginatedData[AdminChapterResponse]],
)
async def list_chapters(
    course_id: int = Path(..., ge=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=100),
    _admin=Depends(require_permission("course:read")),
):
    return success(
        data=await AdminCourseService().list_chapters(
            course_id, None, page=page, page_size=page_size
        )
    )


@router.post(
    "/{course_id}/chapters/batch",
    response_model=APIResponse[list[AdminChapterResponse]],
)
async def batch_create_chapters(
    course_id: int = Path(..., ge=1),
    body: AdminChapterBatchCreate = ...,
    admin=Depends(require_permission("course:write")),
):
    return success(
        data=await AdminCourseService().batch_create_chapters(
            course_id, body.upload_ids, admin_id=admin.id
        ),
        message="章节已批量创建",
    )


@router.post("/{course_id}/chapters/sort", response_model=APIResponse)
async def sort_chapters(
    course_id: int = Path(..., ge=1),
    items: list[AdminChapterSortItem] = Body(...),
    admin=Depends(require_permission("course:write")),
):
    count = await AdminCourseService().sort_chapters(
        course_id, items, admin_id=admin.id
    )
    return success(message=f"已更新 {count} 个章节排序")


@router.put(
    "/{course_id}/chapters/{chapter_id}",
    response_model=APIResponse[AdminChapterResponse],
)
async def update_chapter(
    course_id: int = Path(..., ge=1),
    chapter_id: int = Path(..., ge=1),
    body: AdminChapterUpdate = ...,
    admin=Depends(require_permission("course:write")),
):
    chapter = await AdminCourseService().update_chapter_metadata(
        course_id, chapter_id, body, admin_id=admin.id
    )
    return success(data=chapter)


@router.post(
    "/{course_id}/chapters/{chapter_id}/video",
    response_model=APIResponse[AdminChapterResponse],
)
async def replace_chapter_video(
    course_id: int = Path(..., ge=1),
    chapter_id: int = Path(..., ge=1),
    body: AdminChapterReplaceVideo = ...,
    admin=Depends(require_permission("course:write")),
):
    return success(
        data=await AdminCourseService().replace_chapter_video(
            course_id, chapter_id, body.upload_id, admin_id=admin.id
        ),
        message="章节视频已替换",
    )


@router.delete("/{course_id}/chapters/{chapter_id}", response_model=APIResponse)
async def delete_chapter(
    course_id: int = Path(..., ge=1),
    chapter_id: int = Path(..., ge=1),
    admin=Depends(require_permission("course:write")),
):
    await AdminCourseService().delete_chapter_completely(
        course_id, chapter_id, admin_id=admin.id
    )
    return success(message="章节已删除")


@router.get("/{course_id}/quiz-bindings", response_model=APIResponse[list[AdminCourseBindingResponse]])
async def list_bindings(
    course_id: int = Path(..., ge=1),
    _admin=Depends(require_super_admin),
):
    return success(data=await AdminCourseService().list_bindings(course_id))


@router.get("/{course_id}/quiz-bindings/impact", response_model=APIResponse[AdminCourseBindingImpact])
async def preview_binding(
    course_id: int = Path(..., ge=1),
    library_id: int = Query(..., ge=1),
    _admin=Depends(require_super_admin),
):
    return success(
        data=await CourseEntitlementService.preview_binding(course_id, library_id)
    )


@router.post("/{course_id}/quiz-bindings", response_model=APIResponse[AdminCourseEntitlementJobResponse])
async def create_binding(
    course_id: int = Path(..., ge=1),
    body: AdminCourseBindingCreate = ...,
    admin=Depends(require_super_admin),
):
    if body.backfill_confirmations != ["impact_confirmed"]:
        raise BusinessException("请先确认绑定影响并执行回补")
    return success(
        data=await CourseEntitlementService.create_binding(
            course_id, body.library_id, admin_id=admin.id
        ),
        message="绑定已创建，回补任务已排队",
    )


@router.post("/course-quiz-bindings/{binding_id}/status", response_model=APIResponse[AdminCourseEntitlementJobResponse])
async def set_binding_status(
    binding_id: int = Path(..., ge=1),
    body: AdminCourseBindingStatusRequest = ...,
    admin=Depends(require_super_admin),
):
    return success(
        data=await CourseEntitlementService.set_binding_status(
            binding_id, body.status, admin_id=admin.id
        )
    )


@router.get("/{course_id}/entitlement-jobs", response_model=APIResponse[PaginatedData[AdminCourseEntitlementJobResponse]])
async def list_jobs(
    course_id: int = Path(..., ge=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(require_permission("course:read")),
):
    return success(
        data=await AdminCourseService().list_jobs(
            course_id, page=page, page_size=page_size
        )
    )


@router.post("/course-entitlement-jobs/{job_id}/retry", response_model=APIResponse[AdminCourseEntitlementJobResponse])
async def retry_job(
    job_id: int = Path(..., ge=1),
    admin=Depends(require_super_admin),
):
    return success(
        data=await AdminCourseService().retry_job(job_id, admin_id=admin.id)
    )
