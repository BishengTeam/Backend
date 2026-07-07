from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import require_permission
from app.schemas.admin_course import AdminCourseCreate, AdminCourseListItem, AdminCourseUpdate
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.course import ChapterCreate, ChapterResponse, ChapterSortItem, ChapterUpdate
from app.services.admin_course import AdminCourseService

router = APIRouter(prefix="/courses", tags=["管理后台-课程管理"])


@router.get("",
    response_model=APIResponse[PaginatedData[AdminCourseListItem]],
    summary="课程列表",
    description="""
管理后台 **课程管理** 页面使用。

**页面路径**: `/admin/courses`

**使用场景**: 页面加载时获取课程列表，支持关键词搜索和分类筛选

**查询参数**:
- `keyword`: 按课程标题模糊搜索
- `category`: 按类目筛选
- `page`: 页码，从 1 开始
- `page_size`: 每页条数，默认 20，最大 100

**响应**: 分页课程数据，包含封面、价格、讲师、批次等信息
    """,
)
async def list_courses(
    keyword: str | None = Query(None, description="按标题关键词模糊搜索"),
    category: str | None = Query(None, description="按分类筛选"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(require_permission("course:list")),
) -> APIResponse[PaginatedData[AdminCourseListItem]]:
    result = await AdminCourseService().list_courses(keyword, category, page, page_size)
    return success(data=result)


@router.post("",
    response_model=APIResponse[AdminCourseListItem],
    summary="新增课程",
    description="""
管理后台 **课程管理** 页面使用。

**页面路径**: `/admin/courses`

**使用场景**: 新增/编辑弹窗提交，创建新课程

**请求体**: 课程名称、封面、价格、讲师、批次等信息

**响应**: 新创建的课程详情
    """,
)
async def create_course(
    body: AdminCourseCreate,
    _admin=Depends(require_permission("course:write")),
) -> APIResponse[AdminCourseListItem]:
    result = await AdminCourseService().create_course(body)
    return success(data=result)


# ==================== 章节管理（5 接口） ====================


@router.get("/{course_id}/chapters",
    response_model=APIResponse[PaginatedData[ChapterResponse]],
    summary="章节列表",
    description="""
管理后台 **课程编辑 → 章节管理 Tab** 使用。

**使用场景**: 查看指定课程的所有章节，支持分页和上线状态筛选

**路径参数**:
- `course_id`: 课程 ID

**查询参数**:
- `is_active`: 按上线状态筛选（可选）
- `page`: 页码
- `page_size`: 每页数量
    """,
)
async def list_chapters(
    course_id: int = Path(..., ge=1),
    is_active: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(require_permission("course:write")),
):
    result = await AdminCourseService().list_chapters(course_id, is_active, page, page_size)
    return success(data=result)


@router.post("/{course_id}/chapters",
    response_model=APIResponse[ChapterResponse],
    summary="新增章节",
    description="""
管理后台 **课程编辑 → 章节管理 Tab** 使用。

**使用场景**: 新增章节弹窗提交

**路径参数**:
- `course_id`: 课程 ID

**请求体**: 章节标题、视频 URL、时长、排序
    """,
)
async def create_chapter(
    body: ChapterCreate,
    course_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("course:write")),
):
    result = await AdminCourseService().create_chapter(course_id, body)
    return success(data=result)


@router.put("/{course_id}/chapters/sort",
    response_model=APIResponse[int],
    summary="批量排序",
    description="""
管理后台 **课程编辑 → 章节管理 Tab** 使用。

**使用场景**: 拖拽排序完成后提交新的排序

**路径参数**:
- `course_id`: 课程 ID

**请求体**: 排序项列表 `[{id, sort_order}, ...]`

⚠️ 该路由必须在 `PUT .../chapters/{chapter_id}` 之前注册
    """,
)
async def sort_chapters(
    body: list[ChapterSortItem],
    course_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("course:write")),
):
    count = await AdminCourseService().sort_chapters(course_id, body)
    return success(data=count, message=f"已更新 {count} 个排序")


@router.put("/{course_id}/chapters/{chapter_id}",
    response_model=APIResponse[ChapterResponse],
    summary="编辑章节",
    description="""
管理后台 **课程编辑 → 章节管理 Tab** 使用。

**使用场景**: 编辑章节弹窗提交

**路径参数**:
- `course_id`: 课程 ID
- `chapter_id`: 章节 ID
    """,
)
async def update_chapter(
    body: ChapterUpdate,
    course_id: int = Path(..., ge=1),
    chapter_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("course:write")),
):
    result = await AdminCourseService().update_chapter(chapter_id, body)
    return success(data=result)


@router.delete("/{course_id}/chapters/{chapter_id}",
    response_model=APIResponse,
    summary="下架章节",
    description="""
管理后台 **课程编辑 → 章节管理 Tab** 使用。

**使用场景**: 软删除指定章节

**路径参数**:
- `course_id`: 课程 ID
- `chapter_id`: 章节 ID
    """,
)
async def delete_chapter(
    course_id: int = Path(..., ge=1),
    chapter_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("course:write")),
):
    await AdminCourseService().delete_chapter(chapter_id)
    return success(message="章节已下架")


# ==================== 现有课程管理路由 ====================


@router.put("/{course_id}",
    response_model=APIResponse[AdminCourseListItem],
    summary="编辑课程",
    description="""
管理后台 **课程管理** 页面使用。

**页面路径**: `/admin/courses`

**使用场景**: 新增/编辑弹窗提交，更新已有课程信息

**路径参数**:
- `course_id`: 课程 ID

**请求体**: 要更新的课程字段

**响应**: 更新后的课程详情
    """,
)
async def update_course(
    body: AdminCourseUpdate,
    course_id: int = Path(..., description="课程 ID"),
    _admin=Depends(require_permission("course:write")),
) -> APIResponse[AdminCourseListItem]:
    result = await AdminCourseService().update_course(course_id, body)
    return success(data=result)


@router.delete("/{course_id}",
    response_model=APIResponse,
    summary="下架课程",
    description="""
管理后台 **课程管理** 页面使用。

**页面路径**: `/admin/courses`

**使用场景**: 下架指定课程（软删除）

**路径参数**:
- `course_id`: 课程 ID

**响应**: 操作结果消息
    """,
)
async def delete_course(
    course_id: int = Path(..., description="课程 ID"),
    _admin=Depends(require_permission("course:write")),
):
    await AdminCourseService().deactivate_course(course_id)
    return success(message="课程已下架")
