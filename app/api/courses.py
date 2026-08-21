from fastapi import APIRouter, Depends, Path, Query

from app.domain.user.src.index import User
from app.middleware.auth import get_current_user, get_current_user_optional
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.course import (
    ChapterPlaybackResponse,
    ChapterProgressResponse,
    ChapterProgressUpsert,
    CourseChaptersResponse,
    CourseDetailResponse,
    CourseEnrollRequest,
    CourseEnrollmentResponse,
    CourseFilter,
    CourseListResponse,
    CoursePurchaseRequest,
    CoursePurchaseResponse,
)
from app.services.course import CourseService
from app.services.course_purchase import CoursePurchaseService


router = APIRouter(prefix="/courses", tags=["课程"])


@router.get(
    "",
    response_model=APIResponse[PaginatedData[CourseListResponse]],
    summary="课程列表",
)
async def list_courses(
    category: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
):
    filters = CourseFilter(category=category) if category else None
    return success(
        data=await CourseService().list_courses(filters, page, page_size)
    )


@router.get(
    "/my",
    response_model=APIResponse[PaginatedData[CourseEnrollmentResponse]],
    summary="我的课程",
)
async def my_courses(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
):
    return success(
        data=await CourseService().my_courses(current_user.id, page, page_size)
    )


@router.get(
    "/categories",
    response_model=APIResponse[list[str]],
    summary="课程类目",
)
async def list_categories():
    return success(data=await CourseService().list_categories())


@router.get(
    "/{course_id}",
    response_model=APIResponse[CourseDetailResponse],
    summary="课程详情",
)
async def get_course(
    course_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
):
    return success(
        data=await CourseService().get_course(course_id, current_user.id)
    )


@router.post(
    "/{course_id}/purchase",
    response_model=APIResponse[CoursePurchaseResponse],
    summary="购买课程",
)
async def purchase_course(
    course_id: int = Path(..., ge=1),
    body: CoursePurchaseRequest | None = None,
    current_user: User = Depends(get_current_user),
):
    return success(
        data=await CoursePurchaseService().purchase(
            current_user.id, course_id, allow_paid=True
        )
    )


@router.post(
    "/enroll",
    response_model=APIResponse[CourseEnrollmentResponse],
    summary="免费课程报名",
    deprecated=True,
)
async def enroll_course(
    body: CourseEnrollRequest,
    current_user: User = Depends(get_current_user),
):
    return success(data=await CourseService().enroll(current_user.id, body))


@router.get(
    "/{course_id}/chapters",
    response_model=APIResponse[CourseChaptersResponse],
    summary="课程章节",
)
async def get_chapters(
    course_id: int = Path(..., ge=1),
    current_user: User | None = Depends(get_current_user_optional),
):
    user_id = current_user.id if current_user else None
    return success(data=await CourseService().get_chapters(course_id, user_id))


@router.post(
    "/{course_id}/chapters/{chapter_id}/playback-url",
    response_model=APIResponse[ChapterPlaybackResponse],
    summary="获取章节播放地址",
)
async def issue_playback(
    course_id: int = Path(..., ge=1),
    chapter_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
):
    return success(
        data=await CourseService().issue_playback(
            current_user.id, course_id, chapter_id
        )
    )


@router.get(
    "/{course_id}/progress",
    response_model=APIResponse[ChapterProgressResponse],
    summary="查询学习进度",
)
async def get_progress(
    course_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
):
    return success(
        data=await CourseService().get_progress(current_user.id, course_id)
    )


@router.post(
    "/{course_id}/progress",
    response_model=APIResponse[ChapterProgressResponse],
    summary="上报学习进度",
)
async def upsert_progress(
    body: ChapterProgressUpsert,
    course_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
):
    return success(
        data=await CourseService().upsert_progress(
            current_user.id, course_id, body
        )
    )
