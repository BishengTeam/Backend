from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.ticket import TicketCreateRequest, TicketDetail, TicketListItem
from app.services.ticket import TicketService

router = APIRouter(prefix="/tickets", tags=["工单"])


@router.post("",
    response_model=APIResponse[TicketListItem],
    summary="创建工单",
    description="""
小程序 **工单/反馈** 页面使用。

**使用场景**: 用户提交反馈或问题工单

**请求体**: 工单创建参数，含标题、内容、类型等

**响应**: 新创建的工单信息

**认证**: 需登录
    """,
)
async def create_ticket(
    body: TicketCreateRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[TicketListItem]:
    """创建工单."""
    result = await TicketService().create_ticket(current_user.id, body)
    return success(data=result)


@router.get("",
    response_model=APIResponse[PaginatedData[TicketListItem]],
    summary="工单列表",
    description="""
小程序 **工单/反馈** 页面使用。

**使用场景**: 查看当前用户的工单列表，支持分页

**查询参数**:
- `page`: 页码
- `page_size`: 每页数量

**响应**: 分页工单记录

**认证**: 需登录
    """,
)
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


@router.get("/{id}",
    response_model=APIResponse[TicketDetail],
    summary="工单详情",
    description="""
小程序 **工单/反馈** 页面使用。

**使用场景**: 查看单个工单的详细信息及处理进度

**路径参数**:
- `id`: 工单 ID

**响应**: 工单详情，含状态、回复记录

**认证**: 需登录
    """,
)
async def get_ticket(
    id: int = Path(..., ge=1, description="工单 ID"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[TicketDetail]:
    """工单详情."""
    result = await TicketService().get_ticket(current_user.id, id)
    return success(data=result)