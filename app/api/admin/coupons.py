from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import require_permission
from app.schemas.admin_coupon import AdminCouponBatchCreate, AdminCouponCreate, AdminCouponListItem
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.admin_coupon import AdminCouponService

router = APIRouter(prefix="/coupons", tags=["管理后台-优惠券管理"])


@router.get("",
    response_model=APIResponse[PaginatedData[AdminCouponListItem]],
    summary="优惠券列表",
    description="""
管理后台 **优惠券管理** 页面使用。

**页面路径**: `/admin/coupons`

**使用场景**: 页面加载时获取优惠券列表，支持分页

**查询参数**:
- `page`: 页码
- `page_size`: 每页数量

**响应**: 分页优惠券数据

**认证**: 需 `content:list` 权限
    """,
)
async def list_coupons(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(require_permission("content:list")),
) -> APIResponse[PaginatedData[AdminCouponListItem]]:
    result = await AdminCouponService().list_coupons(page, page_size)
    return success(data=result)


@router.post("",
    response_model=APIResponse[AdminCouponListItem],
    summary="新增优惠券",
    description="""
管理后台 **优惠券管理** 页面使用。

**页面路径**: `/admin/coupons`

**使用场景**: 管理员新增单张优惠券

**响应**: 新创建的优惠券

**认证**: 需 `content:write` 权限
    """,
)
async def create_coupon(
    body: AdminCouponCreate,
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[AdminCouponListItem]:
    result = await AdminCouponService().create(body)
    return success(data=result)


@router.post("/batch",
    response_model=APIResponse[list[AdminCouponListItem]],
    summary="批量创建优惠券",
    description="""
管理后台 **优惠券管理** 页面使用。

**页面路径**: `/admin/coupons`

**使用场景**: 管理员批量生成优惠券码

**响应**: 新创建的优惠券列表

**认证**: 需 `content:write` 权限
    """,
)
async def batch_create_coupons(
    body: AdminCouponBatchCreate,
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[list[AdminCouponListItem]]:
    result = await AdminCouponService().batch_create(body)
    return success(data=result)


@router.delete("/{coupon_id}",
    response_model=APIResponse,
    summary="作废优惠券",
    description="""
管理后台 **优惠券管理** 页面使用。

**页面路径**: `/admin/coupons`

**使用场景**: 管理员作废一张优惠券

**路径参数**:
- `coupon_id`: 优惠券 ID

**认证**: 需 `content:write` 权限
    """,
)
async def delete_coupon(
    coupon_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("content:write")),
):
    await AdminCouponService().deactivate(coupon_id)
    return success(message="优惠券已作废")