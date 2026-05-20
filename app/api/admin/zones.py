from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import get_current_admin
from app.schemas.admin_zone import AdminZoneCreate, AdminZoneListItem, AdminZoneUpdate
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.admin_zone import AdminZoneService

router = APIRouter(prefix="/zones", tags=["管理后台-专区内容"])


@router.get("", response_model=APIResponse[PaginatedData[AdminZoneListItem]])
async def list_zones(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(get_current_admin),
) -> APIResponse[PaginatedData[AdminZoneListItem]]:
    result = await AdminZoneService().list_zones(page, page_size)
    return success(data=result)


@router.post("", response_model=APIResponse[AdminZoneListItem])
async def create_zone(
    body: AdminZoneCreate,
    _admin=Depends(get_current_admin),
) -> APIResponse[AdminZoneListItem]:
    result = await AdminZoneService().create(body)
    return success(data=result)


@router.put("/{zone_id}", response_model=APIResponse[AdminZoneListItem])
async def update_zone(
    body: AdminZoneUpdate,
    zone_id: int = Path(..., ge=1),
    _admin=Depends(get_current_admin),
) -> APIResponse[AdminZoneListItem]:
    result = await AdminZoneService().update(zone_id, body)
    return success(data=result)


@router.delete("/{zone_id}", response_model=APIResponse)
async def delete_zone(
    zone_id: int = Path(..., ge=1),
    _admin=Depends(get_current_admin),
):
    await AdminZoneService().deactivate(zone_id)
    return success(message="专区内容已下架")
