from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import require_permission
from app.schemas.admin_settings import AdminSettingsUserCreate, AdminSettingsUserListItem, AdminSettingsUserUpdate
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.admin_settings import AdminSettingsService

router = APIRouter(prefix="/settings", tags=["管理后台-系统设置"])


@router.get("/admins",
    response_model=APIResponse[PaginatedData[AdminSettingsUserListItem]],
    summary="管理员列表",
    description="""
管理后台 **系统设置** 页面使用。

**页面路径**: `/admin/settings`

**使用场景**: 页面加载时获取管理员列表，支持分页

**查询参数**:
- `page`: 页码
- `page_size`: 每页数量

**响应**: 分页管理员数据

**认证**: 需 `dashboard:view` 权限
    """,
)
async def list_admins(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(require_permission("dashboard:view")),
) -> APIResponse[PaginatedData[AdminSettingsUserListItem]]:
    result = await AdminSettingsService().list_admins(page, page_size)
    return success(data=result)


@router.post("/admins",
    response_model=APIResponse[AdminSettingsUserListItem],
    summary="新增管理员",
    description="""
管理后台 **系统设置** 页面使用。

**页面路径**: `/admin/settings`

**使用场景**: 管理员新增一个后台管理员账号

**响应**: 新创建的管理员

**认证**: 需 `dashboard:view` 权限
    """,
)
async def create_admin(
    body: AdminSettingsUserCreate,
    _admin=Depends(require_permission("dashboard:view")),
) -> APIResponse[AdminSettingsUserListItem]:
    result = await AdminSettingsService().create_admin(body)
    return success(data=result)


@router.put("/admins/{admin_id}",
    response_model=APIResponse[AdminSettingsUserListItem],
    summary="编辑管理员",
    description="""
管理后台 **系统设置** 页面使用。

**页面路径**: `/admin/settings`

**使用场景**: 管理员编辑一个后台管理员账号的信息

**路径参数**:
- `admin_id`: 管理员 ID

**响应**: 更新后的管理员

**认证**: 需 `dashboard:view` 权限
    """,
)
async def update_admin(
    body: AdminSettingsUserUpdate,
    admin_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("dashboard:view")),
) -> APIResponse[AdminSettingsUserListItem]:
    result = await AdminSettingsService().update_admin(admin_id, body)
    return success(data=result)