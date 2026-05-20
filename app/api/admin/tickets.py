from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import get_current_admin
from app.schemas.admin_ticket import AdminTicketFilter, AdminTicketListItem, AdminTicketUpdate
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.admin_ticket import AdminTicketService

router = APIRouter(prefix="/tickets", tags=["管理后台-工单管理"])


@router.get("", response_model=APIResponse[PaginatedData[AdminTicketListItem]])
async def list_tickets(
    status: str | None = Query(None, description="按状态筛选"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(get_current_admin),
) -> APIResponse[PaginatedData[AdminTicketListItem]]:
    filters = AdminTicketFilter(status=status) if status else None
    result = await AdminTicketService().list_tickets(filters, page, page_size)
    return success(data=result)


@router.put("/{ticket_id}", response_model=APIResponse[AdminTicketListItem])
async def update_ticket(
    body: AdminTicketUpdate,
    ticket_id: int = Path(..., ge=1),
    _admin=Depends(get_current_admin),
) -> APIResponse[AdminTicketListItem]:
    result = await AdminTicketService().update_ticket(ticket_id, body)
    return success(data=result)
