from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import get_current_user
from app.models.user import User
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.course import (
    CourseDetailResponse,
    CourseEnrollRequest,
    CourseEnrollmentResponse,
    CourseFilter,
    CourseListResponse,
)
from app.services.course import CourseService

router = APIRouter(prefix="/courses", tags=["课程"])


@router.get("")
async def list_courses(
    category: str | None = Query(None, description="按类目筛选"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[PaginatedData[CourseListResponse]]:
    """课程列表"""
    filters = CourseFilter(category=category) if category else None
    result = await CourseService().list_courses(filters, page, page_size)
    return success(data=result)


@router.get("/my")
async def my_courses(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[PaginatedData[CourseEnrollmentResponse]]:
    """我的课程列表"""
    result = await CourseService().my_courses(current_user.id, page, page_size)
    return success(data=result)


@router.get("/{course_id}")
async def get_course(
    course_id: int = Path(..., description="课程 ID"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[CourseDetailResponse]:
    """课程详情"""
    result = await CourseService().get_course(course_id)
    return success(data=result)


@router.post("/enroll")
async def enroll_course(
    body: CourseEnrollRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[CourseEnrollmentResponse]:
    """课程报名"""
    result = await CourseService().enroll(current_user.id, body)
    return success(data=result)
