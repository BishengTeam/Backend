from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import require_permission
from app.schemas.admin import AdminBatchDeleteRequest
from app.schemas.admin_zone import AdminZoneCreate, AdminZoneListItem, AdminZoneSortItem, AdminZoneStatusToggle, AdminZoneUpdate
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.admin_zone import AdminZoneService

router = APIRouter(prefix="/zones", tags=["管理后台-专区内容"])


@router.get("", response_model=APIResponse[PaginatedData[AdminZoneListItem]])
async def list_zones(
    keyword: str | None = Query(None, description="按标题关键词模糊搜索"),
    zone_type: str | None = Query(None, description="按专区类型筛选"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[PaginatedData[AdminZoneListItem]]:
    result = await AdminZoneService().list_zones(keyword, zone_type, page, page_size)
    return success(data=result)


@router.post("", response_model=APIResponse[AdminZoneListItem])
async def create_zone(
    body: AdminZoneCreate,
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[AdminZoneListItem]:
    result = await AdminZoneService().create(body)
    return success(data=result)


@router.put("/sort", response_model=APIResponse[int])
async def update_zones_sort(
    body: list[AdminZoneSortItem],
    _admin=Depends(require_permission("content:write")),
):
    updates = [item.model_dump() for item in body]
    count = await AdminZoneService().update_sort(updates)
    return success(data=count, message=f"已更新 {count} 个排序")


@router.put("/{zone_id}", response_model=APIResponse[AdminZoneListItem])
async def update_zone(
    body: AdminZoneUpdate,
    zone_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[AdminZoneListItem]:
    result = await AdminZoneService().update(zone_id, body)
    return success(data=result)


@router.patch("/{zone_id}/status", response_model=APIResponse[AdminZoneListItem])
async def toggle_zone_status(
    body: AdminZoneStatusToggle,
    zone_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[AdminZoneListItem]:
    result = await AdminZoneService().toggle_status(zone_id, body.is_active)
    return success(data=result)


@router.delete("/{zone_id}", response_model=APIResponse)
async def delete_zone(
    zone_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("content:write")),
):
    await AdminZoneService().deactivate(zone_id)
    return success(message="专区内容已下架")


@router.post("/batch-delete", response_model=APIResponse[int])
async def batch_delete_zones(
    body: AdminBatchDeleteRequest,
    _admin=Depends(require_permission("content:write")),
):
    count = await AdminZoneService().batch_deactivate(body.ids)
    return success(data=count, message=f"已下架 {count} 个内容")