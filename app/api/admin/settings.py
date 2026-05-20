from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import get_current_admin
from app.schemas.admin_settings import AdminSettingsUserCreate, AdminSettingsUserListItem, AdminSettingsUserUpdate
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.admin_settings import AdminSettingsService

router = APIRouter(prefix="/settings", tags=["管理后台-系统设置"])


@router.get("/admins", response_model=APIResponse[PaginatedData[AdminSettingsUserListItem]])
async def list_admins(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(get_current_admin),
) -> APIResponse[PaginatedData[AdminSettingsUserListItem]]:
    result = await AdminSettingsService().list_admins(page, page_size)
    return success(data=result)


@router.post("/admins", response_model=APIResponse[AdminSettingsUserListItem])
async def create_admin(
    body: AdminSettingsUserCreate,
    _admin=Depends(get_current_admin),
) -> APIResponse[AdminSettingsUserListItem]:
    result = await AdminSettingsService().create_admin(body)
    return success(data=result)


@router.put("/admins/{admin_id}", response_model=APIResponse[AdminSettingsUserListItem])
async def update_admin(
    body: AdminSettingsUserUpdate,
    admin_id: int = Path(..., ge=1),
    _admin=Depends(get_current_admin),
) -> APIResponse[AdminSettingsUserListItem]:
    result = await AdminSettingsService().update_admin(admin_id, body)
    return success(data=result)
