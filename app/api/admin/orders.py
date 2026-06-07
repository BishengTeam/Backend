from datetime import datetime

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import Response

from app.middleware.auth import require_permission
from app.middleware.rate_limit import limiter
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.order import OrderDetailResponse, OrderFilter, OrderResponse, OrderStatus
from app.services.admin_order import AdminOrderService

router = APIRouter(prefix="/orders", tags=["管理后台-订单管理"])


@router.get("",
    response_model=APIResponse[PaginatedData[OrderResponse]],
    summary="订单列表",
    description="""
管理后台 **订单管理** 页面使用。

**页面路径**: `/admin/orders`

**使用场景**: 页面加载时获取订单列表，支持多条件筛选

**查询参数**:
- `status`: 按状态筛选：pending / paid / completed / refunded / closed
- `cert_type`: 按认证类型筛选
- `phone`: 按考生手机号筛选
- `start_time`: 创建时间起，ISO 8601
- `end_time`: 创建时间止，ISO 8601
- `page`: 页码，从 1 开始
- `page_size`: 每页条数，默认 20，最大 100

**响应**: 分页订单数据，包含订单号、金额、状态、考生信息等
    """,
)
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
    _admin=Depends(require_permission("order:list")),
) -> APIResponse[PaginatedData[OrderResponse]]:
    filters = OrderFilter(status=status, cert_type=cert_type, phone=phone) if (status or cert_type or phone) else None
    result = await AdminOrderService().list_orders(filters, start_time, end_time, page, page_size)
    return success(data=result)


@router.get("/export",
    response_class=Response,
    summary="导出订单数据",
    description="""
管理后台 **订单管理** 页面使用。

**页面路径**: `/admin/orders`

**使用场景**: 导出筛选后的订单数据为 CSV 文件

**查询参数**:
- `status`: 按状态筛选
- `cert_type`: 按认证类型筛选
- `phone`: 按考生手机号筛选
- `start_time`: 创建时间起，ISO 8601
- `end_time`: 创建时间止，ISO 8601

**响应**: CSV 文件下载
    """,
)
async def export_orders(
    status: OrderStatus | None = Query(None, description="按状态筛选"),
    cert_type: str | None = Query(None, description="按认证类型筛选"),
    phone: str | None = Query(None, description="按考生手机号筛选"),
    start_time: datetime | None = Query(None, description="创建时间起，ISO 8601"),
    end_time: datetime | None = Query(None, description="创建时间止，ISO 8601"),
    _admin=Depends(require_permission("order:list")),
):
    filters = OrderFilter(status=status, cert_type=cert_type, phone=phone) if (status or cert_type or phone) else None
    csv_content = await AdminOrderService().export_orders(filters, start_time, end_time)
    return Response(content=csv_content, media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=orders.csv"})


@router.get("/reconciliation",
    response_model=APIResponse[dict],
    summary="订单对账",
    description="""
管理后台 **订单管理** 页面使用。

**页面路径**: `/admin/orders`

**使用场景**: 按日期进行订单对账，统计当日订单金额和数量

**查询参数**:
- `date`: 对账日期，YYYY-MM-DD

**响应**: 对账统计数据
    """,
)
async def reconciliation(
    date: str = Query(..., description="对账日期，YYYY-MM-DD"),
    _admin=Depends(require_permission("order:list")),
):
    result = await AdminOrderService().reconciliation(date)
    return success(data=result)


@router.get("/{order_id}",
    response_model=APIResponse[OrderDetailResponse],
    summary="订单详情",
    description="""
管理后台 **订单管理** 页面使用。

**页面路径**: `/admin/orders`

**使用场景**: 查看指定订单的详细信息

**路径参数**:
- `order_id`: 订单 ID

**响应**: 订单详情，包含订单信息、考生信息、支付记录等
    """,
)
async def get_order(
    order_id: int = Path(..., description="订单 ID"),
    _admin=Depends(require_permission("order:list")),
) -> APIResponse[OrderDetailResponse]:
    result = await AdminOrderService().get_order(order_id)
    return success(data=result)


@router.post("/{order_id}/refund",
    response_model=APIResponse[OrderDetailResponse],
    summary="订单退款",
    description="""
管理后台 **订单管理** 页面使用。

**页面路径**: `/admin/orders`

**使用场景**: 对指定订单执行退款操作

**路径参数**:
- `order_id`: 订单 ID

**响应**: 退款后的订单详情
    """,
)
@limiter.limit("10/minute")
async def refund_order(
    request: Request,
    order_id: int = Path(..., description="订单 ID"),
    _admin=Depends(require_permission("order:write")),
) -> APIResponse[OrderDetailResponse]:
    result = await AdminOrderService().refund_order(order_id)
    return success(data=result)