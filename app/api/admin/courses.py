from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import get_current_admin
from app.schemas.admin_course import AdminCourseCreate, AdminCourseListItem, AdminCourseUpdate
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.admin_course import AdminCourseService

router = APIRouter(prefix="/courses", tags=["管理后台-课程管理"])


@router.get("", response_model=APIResponse[PaginatedData[AdminCourseListItem]])
async def list_courses(
    keyword: str | None = Query(None, description="按标题关键词模糊搜索"),
    category: str | None = Query(None, description="按分类筛选"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(get_current_admin),
) -> APIResponse[PaginatedData[AdminCourseListItem]]:
    result = await AdminCourseService().list_courses(keyword, category, page, page_size)
    return success(data=result)


@router.post("", response_model=APIResponse[AdminCourseListItem])
async def create_course(
    body: AdminCourseCreate,
    _admin=Depends(get_current_admin),
) -> APIResponse[AdminCourseListItem]:
    result = await AdminCourseService().create_course(body)
    return success(data=result)


@router.put("/{course_id}", response_model=APIResponse[AdminCourseListItem])
async def update_course(
    body: AdminCourseUpdate,
    course_id: int = Path(..., description="课程 ID"),
    _admin=Depends(get_current_admin),
) -> APIResponse[AdminCourseListItem]:
    result = await AdminCourseService().update_course(course_id, body)
    return success(data=result)


@router.delete("/{course_id}", response_model=APIResponse)
async def delete_course(
    course_id: int = Path(..., description="课程 ID"),
    _admin=Depends(get_current_admin),
):
    await AdminCourseService().deactivate_course(course_id)
    return success(message="课程已下架")
