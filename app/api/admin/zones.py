from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import require_permission
from app.schemas.admin import AdminBatchDeleteRequest
from app.schemas.admin_zone import AdminZoneCreate, AdminZoneListItem, AdminZoneSortItem, AdminZoneStatusToggle, AdminZoneUpdate
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.admin_zone import AdminZoneService

router = APIRouter(prefix="/zones", tags=["管理后台-专区内容"])


@router.get("",
    response_model=APIResponse[PaginatedData[AdminZoneListItem]],
    summary="专区内容列表",
    description="""
管理后台 **内容管理** 页面使用。

**页面路径**: `/admin/content`
**使用场景**: 页面加载时获取专区内容列表，支持关键词搜索和专区类型筛选
**查询参数**:
- `keyword`: 按标题关键词模糊搜索
- `zone_type`: 按专区类型筛选（study / activity / h3c / sangfor 等）
- `page`: 页码，从 1 开始
- `page_size`: 每页条数，默认 20，最大 100
**响应**: 分页专区内容数据，包含封面、外链、Banner状态、有效期等
    """,
)
async def list_zones(
    keyword: str | None = Query(None, description="按标题关键词模糊搜索"),
    zone_type: str | None = Query(None, description="按专区类型筛选"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[PaginatedData[AdminZoneListItem]]:
    result = await AdminZoneService().list_zones(keyword, zone_type, page, page_size)
    return success(data=result)


@router.post("",
    response_model=APIResponse[AdminZoneListItem],
    summary="新增专区内容",
    description="""
管理后台 **内容管理** 页面使用。

**页面路径**: `/admin/content`
**使用场景**: 新增/编辑抽屉提交，创建新的专区内容
**请求体**: 标题、封面、专区类型、描述、外链、排序、Banner开关、有效期等
**响应**: 新创建的专区内容详情
    """,
)
async def create_zone(
    body: AdminZoneCreate,
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[AdminZoneListItem]:
    result = await AdminZoneService().create(body)
    return success(data=result)


@router.put("/sort",
    response_model=APIResponse[int],
    summary="更新专区排序",
    description="""
管理后台 **内容管理** 页面使用。

**页面路径**: `/admin/content`
**使用场景**: 批量更新多个专区内容的排序权重
**请求体**: 排序项列表，每项包含 id 和新的 sort_order
**响应**: 实际更新的条目数量
    """,
)
async def update_zones_sort(
    body: list[AdminZoneSortItem],
    _admin=Depends(require_permission("content:write")),
):
    updates = [item.model_dump() for item in body]
    count = await AdminZoneService().update_sort(updates)
    return success(data=count, message=f"已更新 {count} 个排序")


@router.put("/{zone_id}",
    response_model=APIResponse[AdminZoneListItem],
    summary="编辑专区内容",
    description="""
管理后台 **内容管理** 页面使用。

**页面路径**: `/admin/content`
**使用场景**: 新增/编辑抽屉提交，更新已有专区内容信息
**路径参数**:
- `zone_id`: 专区内容 ID
**请求体**: 要更新的专区内容字段
**响应**: 更新后的专区内容详情
    """,
)
async def update_zone(
    body: AdminZoneUpdate,
    zone_id: int = Path(..., ge=1, description="专区内容 ID"),
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[AdminZoneListItem]:
    result = await AdminZoneService().update(zone_id, body)
    return success(data=result)


@router.patch("/{zone_id}/status",
    response_model=APIResponse[AdminZoneListItem],
    summary="切换专区状态",
    description="""
管理后台 **内容管理** 页面使用。

**页面路径**: `/admin/content`
**使用场景**: 列表中的上架/下架开关，即时切换专区内容的启用状态
**路径参数**:
- `zone_id`: 专区内容 ID
**请求体**: is_active 布尔值
**响应**: 更新后的专区内容详情
    """,
)
async def toggle_zone_status(
    body: AdminZoneStatusToggle,
    zone_id: int = Path(..., ge=1, description="专区内容 ID"),
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[AdminZoneListItem]:
    result = await AdminZoneService().toggle_status(zone_id, body.is_active)
    return success(data=result)


@router.delete("/{zone_id}",
    response_model=APIResponse,
    summary="下架专区内容",
    description="""
管理后台 **内容管理** 页面使用。

**页面路径**: `/admin/content`
**使用场景**: 下架指定专区内容（软删除）
**路径参数**:
- `zone_id`: 专区内容 ID
**响应**: 操作结果消息
    """,
)
async def delete_zone(
    zone_id: int = Path(..., ge=1, description="专区内容 ID"),
    _admin=Depends(require_permission("content:write")),
):
    await AdminZoneService().deactivate(zone_id)
    return success(message="专区内容已下架")


@router.post("/batch-delete",
    response_model=APIResponse[int],
    summary="批量下架专区内容",
    description="""
管理后台 **内容管理** 页面使用。

**页面路径**: `/admin/content`
**使用场景**: 勾选多条内容后批量下架（软删除）
**请求体**: ids 列表
**响应**: 实际下架的条目数量
    """,
)
async def batch_delete_zones(
    body: AdminBatchDeleteRequest,
    _admin=Depends(require_permission("content:write")),
):
    count = await AdminZoneService().batch_deactivate(body.ids)
    return success(data=count, message=f"已下架 {count} 个内容")
