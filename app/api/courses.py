from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
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


@router.get("",
    response_model=APIResponse[PaginatedData[CourseListResponse]],
    summary="课程列表",
    description="""
小程序 **学习专区** 页面使用。

**使用场景**: 加载课程列表，支持按类目筛选

**查询参数**:
- `category`: 类目筛选（可选）
- `page`: 页码
- `page_size`: 每页数量

**认证**: 需登录
    """,
)
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


@router.get("/my",
    response_model=APIResponse[PaginatedData[CourseEnrollmentResponse]],
    summary="我的课程",
    description="""
小程序 **我的** 页面使用。

**使用场景**: 查看当前用户已报名的课程列表

**查询参数**:
- `page`: 页码
- `page_size`: 每页数量

**认证**: 需登录
    """,
)
async def my_courses(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[PaginatedData[CourseEnrollmentResponse]]:
    """我的课程列表"""
    result = await CourseService().my_courses(current_user.id, page, page_size)
    return success(data=result)


@router.get("/categories",
    response_model=APIResponse[list[str]],
    summary="课程类目",
    description="""
小程序 **学习专区** 页面使用。

**使用场景**: 获取所有不重复的课程类目列表

**认证**: 无需登录
    """,
)
async def list_categories() -> APIResponse[list[str]]:
    """获取所有不重复的课程类目"""
    result = await CourseService().list_categories()
    return success(data=result)


@router.get("/{course_id}",
    response_model=APIResponse[CourseDetailResponse],
    summary="课程详情",
    description="""
小程序 **课程详情** 页面使用。

**使用场景**: 查看课程详细信息

**路径参数**:
- `course_id`: 课程 ID

**认证**: 需登录
    """,
)
async def get_course(
    course_id: int = Path(..., description="课程 ID"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[CourseDetailResponse]:
    """课程详情"""
    result = await CourseService().get_course(course_id)
    return success(data=result)


@router.post("/enroll",
    response_model=APIResponse[CourseEnrollmentResponse],
    summary="课程报名",
    description="""
小程序 **课程详情** 页面使用。

**使用场景**: 用户报名课程

**请求体**:
- `course_id`: 课程 ID
- `name`: 报名人姓名
- `phone`: 联系电话

**认证**: 需登录
    """,
)
async def enroll_course(
    body: CourseEnrollRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[CourseEnrollmentResponse]:
    """课程报名"""
    result = await CourseService().enroll(current_user.id, body)
    return success(data=result)