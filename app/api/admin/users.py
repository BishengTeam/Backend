from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import get_current_admin
from app.schemas.admin import AdminUserFilter, AdminUserListItem, AdminUserUpdate
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.admin_user import AdminUserService

router = APIRouter(prefix="/users", tags=["管理后台-用户管理"])


@router.get("", response_model=APIResponse[PaginatedData[AdminUserListItem]])
async def list_users(
    openid: str | None = Query(None, description="按 openid 精确筛选"),
    phone: str | None = Query(None, description="按手机号精确筛选"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(get_current_admin),
) -> APIResponse[PaginatedData[AdminUserListItem]]:
    filters = AdminUserFilter(openid=openid, phone=phone) if (openid or phone) else None
    result = await AdminUserService().list_users(filters, page, page_size)
    return success(data=result)


@router.get("/{user_id}", response_model=APIResponse[AdminUserListItem])
async def get_user(
    user_id: int = Path(..., description="用户 ID"),
    _admin=Depends(get_current_admin),
) -> APIResponse[AdminUserListItem]:
    result = await AdminUserService().get_user(user_id)
    return success(data=result)


@router.put("/{user_id}", response_model=APIResponse[AdminUserListItem])
async def update_user(
    body: AdminUserUpdate,
    user_id: int = Path(..., description="用户 ID"),
    _admin=Depends(get_current_admin),
) -> APIResponse[AdminUserListItem]:
    result = await AdminUserService().update_user(user_id, body)
    return success(data=result)
