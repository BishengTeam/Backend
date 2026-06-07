from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import PlainTextResponse

from app.middleware.auth import require_permission
from app.schemas.admin_activity import AdminActivityCreate, AdminActivityListItem, AdminActivityUpdate
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.activity import ActivityService
from app.services.admin_activity import AdminActivityService

router = APIRouter(prefix="/activities", tags=["管理后台-活动管理"])


@router.get("",
    response_model=APIResponse[PaginatedData[AdminActivityListItem]],
    summary="活动列表",
    description="""
管理后台 **活动管理** 页面使用。

**页面路径**: `/admin/activity`

**使用场景**: 页面加载时获取活动列表，支持关键词搜索

**查询参数**:
- `keyword`: 按活动标题模糊搜索
- `page`: 页码，从 1 开始
- `page_size`: 每页条数，默认 20，最大 100

**响应**: 分页活动数据，包含封面、时间、地点等信息
    """,
)
async def list_activities(
    keyword: str | None = Query(None, description="按标题关键词模糊搜索"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(require_permission("content:list")),
) -> APIResponse[PaginatedData[AdminActivityListItem]]:
    """活动列表（分页+搜索）"""
    result = await AdminActivityService().list_activities(keyword, page, page_size)
    return success(data=result)


@router.post("",
    response_model=APIResponse[AdminActivityListItem],
    summary="新增活动",
    description="""
管理后台 **活动管理** 页面使用。

**页面路径**: `/admin/activity`

**使用场景**: 新增/编辑弹窗提交，创建新活动

**请求体**: 活动名称、封面、时间、地点等信息

**响应**: 新创建的活动详情
    """,
)
async def create_activity(
    body: AdminActivityCreate,
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[AdminActivityListItem]:
    """新增活动"""
    result = await AdminActivityService().create(body)
    return success(data=result)


@router.put("/{activity_id}",
    response_model=APIResponse[AdminActivityListItem],
    summary="编辑活动",
    description="""
管理后台 **活动管理** 页面使用。

**页面路径**: `/admin/activity`

**使用场景**: 新增/编辑弹窗提交，更新已有活动信息

**路径参数**:
- `activity_id`: 活动 ID

**请求体**: 要更新的活动字段

**响应**: 更新后的活动详情
    """,
)
async def update_activity(
    body: AdminActivityUpdate,
    activity_id: int = Path(..., description="活动 ID"),
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[AdminActivityListItem]:
    """更新活动"""
    result = await AdminActivityService().update(activity_id, body)
    return success(data=result)


@router.delete("/{activity_id}",
    response_model=APIResponse,
    summary="下架活动",
    description="""
管理后台 **活动管理** 页面使用。

**页面路径**: `/admin/activity`

**使用场景**: 下架指定活动（软删除）

**路径参数**:
- `activity_id`: 活动 ID

**响应**: 操作结果消息
    """,
)
async def delete_activity(
    activity_id: int = Path(..., description="活动 ID"),
    _admin=Depends(require_permission("content:write")),
):
    """下架活动"""
    await AdminActivityService().deactivate(activity_id)
    return success(message="活动已下架")


@router.get("/export",
    response_class=PlainTextResponse,
    summary="导出活动报名数据",
    description="""
管理后台 **活动管理** 页面使用。

**页面路径**: `/admin/activity`

**使用场景**: 导出所有活动报名数据为 CSV 文件

**响应**: CSV 文件下载
    """,
)
async def export_registrations(
    _admin=Depends(require_permission("content:read")),
):
    """导出活动报名 CSV"""
    csv_content = await ActivityService().export_csv()
    return PlainTextResponse(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=activity_registrations.csv"},
    )