from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
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


@router.post("",
    response_model=APIResponse[OrderResponse],
    summary="创建订单",
    description="""
小程序 **订单** 页面使用。

**使用场景**: 用户下单（认证报名锁定库存，课程购买直接创建）

**请求体**:
- `order_kind`: 订单类型 certification / course
- `product_type`: 商品类型代码
- `candidate_*`: 考生信息（认证报名时必填）
- `extra_data`: 差异化数据

**认证**: 需登录
    """,
)
async def create_order(
    body: OrderCreate,
    current_user: User = Depends(get_current_user),
) -> APIResponse[OrderResponse]:
    """创建订单并锁定库存"""
    result = await OrderService().create_order(current_user.id, body)
    return success(data=result)


@router.get("",
    response_model=APIResponse[PaginatedData[OrderResponse]],
    summary="订单列表",
    description="""
小程序 **订单** 页面使用。

**使用场景**: 查看当前用户的订单列表

**查询参数**:
- `status`: 按状态筛选（pending / paid / completed / refunded / closed）
- `page`: 页码
- `page_size`: 每页数量

**响应**: 订单列表，含金额、状态、时间

**认证**: 需登录
    """,
)
async def list_orders(
    status: OrderStatus | None = Query(
        None, description="按状态筛选：pending / paid / completed / refunded / closed"
    ),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[PaginatedData[OrderResponse]]:
    """订单列表"""
    filters = OrderFilter(status=status) if status else None
    result = await OrderService().list_orders(current_user.id, filters, page, page_size)
    return success(data=result)


@router.get("/{order_id}",
    response_model=APIResponse[OrderDetailResponse],
    summary="订单详情",
    description="""
小程序 **订单详情** 页面使用。

**使用场景**: 查看单个订单的详细信息

**路径参数**:
- `order_id`: 订单 ID

**认证**: 需登录
    """,
)
async def get_order(
    order_id: int = Path(..., description="订单 ID"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[OrderDetailResponse]:
    """订单详情"""
    result = await OrderService().get_order(current_user.id, order_id)
    return success(data=result)