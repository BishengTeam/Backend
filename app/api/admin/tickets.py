from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import require_permission
from app.schemas.admin_ticket import AdminTicketFilter, AdminTicketListItem, AdminTicketUpdate
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.admin_ticket import AdminTicketService

router = APIRouter(prefix="/tickets", tags=["管理后台-工单管理"])


@router.get("",
    response_model=APIResponse[PaginatedData[AdminTicketListItem]],
    summary="工单列表",
    description="""
管理后台 **工单管理** 页面使用。

**页面路径**: `/admin/tickets`

**使用场景**: 页面加载时获取工单列表，支持按状态筛选和分页

**查询参数**:
- `status`: 按状态筛选
- `page`: 页码
- `page_size`: 每页数量

**响应**: 分页工单数据

**认证**: 需 `user:list` 权限
    """,
)
async def list_tickets(
    status: str | None = Query(None, description="按状态筛选"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(require_permission("user:list")),
) -> APIResponse[PaginatedData[AdminTicketListItem]]:
    filters = AdminTicketFilter(status=status) if status else None
    result = await AdminTicketService().list_tickets(filters, page, page_size)
    return success(data=result)


@router.put("/{ticket_id}",
    response_model=APIResponse[AdminTicketListItem],
    summary="处理工单",
    description="""
管理后台 **工单管理** 页面使用。

**页面路径**: `/admin/tickets`

**使用场景**: 管理员处理工单，更新状态或回复内容

**路径参数**:
- `ticket_id`: 工单 ID

**响应**: 更新后的工单

**认证**: 需 `user:list` 权限
    """,
)
async def update_ticket(
    body: AdminTicketUpdate,
    ticket_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("user:list")),
) -> APIResponse[AdminTicketListItem]:
    result = await AdminTicketService().update_ticket(ticket_id, body)
    return success(data=result)