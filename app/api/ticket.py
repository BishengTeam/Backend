from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import get_current_user
from app.models.user import User
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.ticket import TicketCreateRequest, TicketDetail, TicketListItem
from app.services.ticket import TicketService

router = APIRouter(prefix="/tickets", tags=["工单"])


@router.post("", response_model=APIResponse[TicketListItem])
async def create_ticket(
    body: TicketCreateRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[TicketListItem]:
    """创建工单."""
    result = await TicketService().create_ticket(current_user.id, body)
    return success(data=result)


@router.get("", response_model=APIResponse[PaginatedData[TicketListItem]])
async def list_tickets(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Page size"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[PaginatedData[TicketListItem]]:
    """我的工单列表."""
    result = await TicketService().list_tickets(
        current_user.id, page=page, page_size=page_size,
    )
    return success(data=result)


@router.get("/{id}", response_model=APIResponse[TicketDetail])
async def get_ticket(
    id: int = Path(..., ge=1, description="工单 ID"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[TicketDetail]:
    """工单详情."""
    result = await TicketService().get_ticket(current_user.id, id)
    return success(data=result)
