from datetime import datetime

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import Response

from app.middleware.auth import get_current_admin
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.order import OrderDetailResponse, OrderFilter, OrderResponse, OrderStatus
from app.services.admin_order import AdminOrderService

router = APIRouter(prefix="/orders", tags=["管理后台-订单管理"])


@router.get("", response_model=APIResponse[PaginatedData[OrderResponse]])
async def list_orders(
    status: OrderStatus | None = Query(
        None, description="按状态筛选：pending / paid / completed / refunded / closed"
    ),
    cert_type: str | None = Query(None, description="按认证类型筛选"),
    phone: str | None = Query(None, description="按考生手机号筛选"),
    start_time: datetime | None = Query(None, description="创建时间起，ISO 8601"),
    end_time: datetime | None = Query(None, description="创建时间止，ISO 8601"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(get_current_admin),
) -> APIResponse[PaginatedData[OrderResponse]]:
    filters = OrderFilter(status=status, cert_type=cert_type, phone=phone) if (status or cert_type or phone) else None
    result = await AdminOrderService().list_orders(filters, start_time, end_time, page, page_size)
    return success(data=result)


@router.get("/export", response_class=Response)
async def export_orders(
    status: OrderStatus | None = Query(None, description="按状态筛选"),
    cert_type: str | None = Query(None, description="按认证类型筛选"),
    phone: str | None = Query(None, description="按考生手机号筛选"),
    start_time: datetime | None = Query(None, description="创建时间起，ISO 8601"),
    end_time: datetime | None = Query(None, description="创建时间止，ISO 8601"),
    _admin=Depends(get_current_admin),
):
    filters = OrderFilter(status=status, cert_type=cert_type, phone=phone) if (status or cert_type or phone) else None
    csv_content = await AdminOrderService().export_orders(filters, start_time, end_time)
    return Response(content=csv_content, media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=orders.csv"})


@router.get("/reconciliation", response_model=APIResponse[dict])
async def reconciliation(
    date: str = Query(..., description="对账日期，YYYY-MM-DD"),
    _admin=Depends(get_current_admin),
):
    result = await AdminOrderService().reconciliation(date)
    return success(data=result)


@router.get("/{order_id}", response_model=APIResponse[OrderDetailResponse])
async def get_order(
    order_id: int = Path(..., description="订单 ID"),
    _admin=Depends(get_current_admin),
) -> APIResponse[OrderDetailResponse]:
    result = await AdminOrderService().get_order(order_id)
    return success(data=result)


@router.post("/{order_id}/refund", response_model=APIResponse[OrderDetailResponse])
async def refund_order(
    order_id: int = Path(..., description="订单 ID"),
    _admin=Depends(get_current_admin),
) -> APIResponse[OrderDetailResponse]:
    result = await AdminOrderService().refund_order(order_id)
    return success(data=result)