from datetime import datetime

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import Response

from app.middleware.auth import require_permission
from app.schemas.admin import AdminBatchDeleteRequest, AdminUserFilter, AdminUserListItem, AdminUserUpdate
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.admin_user import AdminUserService

router = APIRouter(prefix="/users", tags=["管理后台-用户管理"])


@router.get("", response_model=APIResponse[PaginatedData[AdminUserListItem]])
async def list_users(
    openid: str | None = Query(None, description="按 openid 精确筛选"),
    phone: str | None = Query(None, description="按手机号精确筛选"),
    created_at_start: datetime | None = Query(None, description="创建时间起，ISO 8601"),
    created_at_end: datetime | None = Query(None, description="创建时间止，ISO 8601"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(require_permission("user:list")),
) -> APIResponse[PaginatedData[AdminUserListItem]]:
    has_filters = openid or phone or created_at_start or created_at_end
    filters = AdminUserFilter(openid=openid, phone=phone, created_at_start=created_at_start, created_at_end=created_at_end) if has_filters else None
    result = await AdminUserService().list_users(filters, page, page_size)
    return success(data=result)


@router.get("/export", response_class=Response)
async def export_users(
    openid: str | None = Query(None, description="按 openid 精确筛选"),
    phone: str | None = Query(None, description="按手机号精确筛选"),
    created_at_start: datetime | None = Query(None, description="创建时间起，ISO 8601"),
    created_at_end: datetime | None = Query(None, description="创建时间止，ISO 8601"),
    _admin=Depends(require_permission("user:list")),
):
    has_filters = openid or phone or created_at_start or created_at_end
    filters = AdminUserFilter(openid=openid, phone=phone, created_at_start=created_at_start, created_at_end=created_at_end) if has_filters else None
    csv_content = await AdminUserService().export_users(filters)
    return Response(content=csv_content, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=users.csv"})


@router.get("/{user_id}", response_model=APIResponse[AdminUserListItem])
async def get_user(
    user_id: int = Path(..., description="用户 ID"),
    _admin=Depends(require_permission("user:list")),
) -> APIResponse[AdminUserListItem]:
    result = await AdminUserService().get_user(user_id)
    return success(data=result)


@router.put("/{user_id}", response_model=APIResponse[AdminUserListItem])
async def update_user(
    body: AdminUserUpdate,
    user_id: int = Path(..., description="用户 ID"),
    _admin=Depends(require_permission("user:list")),
) -> APIResponse[AdminUserListItem]:
    result = await AdminUserService().update_user(user_id, body)
    return success(data=result)


@router.post("/batch-delete", response_model=APIResponse[int])
async def batch_delete_users(
    body: AdminBatchDeleteRequest,
    _admin=Depends(require_permission("user:list")),
):
    count = await AdminUserService().batch_delete(body.ids)
    return success(data=count, message=f"已删除 {count} 个用户")


@router.get("/{user_id}/orders", response_model=APIResponse[list[dict]])
async def get_user_orders(
    user_id: int = Path(..., description="用户 ID"),
    _admin=Depends(require_permission("user:list")),
):
    result = await AdminUserService().get_user_orders(user_id)
    return success(data=result)


@router.get("/{user_id}/conversations", response_model=APIResponse[list[dict]])
async def get_user_conversations(
    user_id: int = Path(..., description="用户 ID"),
    _admin=Depends(require_permission("user:list")),
):
    result = await AdminUserService().get_user_conversations(user_id)
    return success(data=result)