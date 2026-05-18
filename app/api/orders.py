from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import get_current_user, require_identity
from app.models.user import User
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.order import (
    OrderCreate,
    OrderDetailResponse,
    OrderFilter,
    OrderResponse,
    OrderStatus,
)
from app.services.order import OrderService

router = APIRouter(prefix="/orders", tags=["订单"])


@router.post("", response_model=APIResponse[OrderResponse])
async def create_order(
    body: OrderCreate,
    current_user: User = Depends(require_identity),
) -> APIResponse[OrderResponse]:
    """创建订单"""
    result = await OrderService().create_order(current_user.id, body)
    return success(data=result)


@router.get("", response_model=APIResponse[PaginatedData[OrderResponse]])
async def list_orders(
    status: OrderStatus | None = Query(
        None, description="按状态筛选：pending / paid / completed / refunded"
    ),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[PaginatedData[OrderResponse]]:
    """订单列表"""
    filters = OrderFilter(status=status) if status else None
    result = await OrderService().list_orders(current_user.id, filters, page, page_size)
    return success(data=result)


@router.get("/{order_id}", response_model=APIResponse[OrderDetailResponse])
async def get_order(
    order_id: int = Path(..., description="订单 ID"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[OrderDetailResponse]:
    """订单详情"""
    result = await OrderService().get_order(current_user.id, order_id)
    return success(data=result)
