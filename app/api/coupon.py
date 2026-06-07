from fastapi import APIRouter, Depends, Query

from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.coupon import CouponAssignRequest, CouponResponse, CouponVerifyRequest
from app.services.coupon import CouponService

router = APIRouter(prefix="/coupons", tags=["优惠券"])
_service = CouponService()


@router.get("",
    response_model=APIResponse[PaginatedData[CouponResponse]],
    summary="优惠券列表",
    description="""
小程序 **优惠券** 页面使用。

**使用场景**: 查看当前用户的优惠券列表，支持按状态筛选和分页

**查询参数**:
- `status`: 状态筛选（unused / used / expired）
- `page`: 页码
- `page_size`: 每页数量

**响应**: 分页优惠券数据，含面额、使用条件、有效期

**认证**: 需登录
    """,
)
async def list_coupons(
    status: str | None = Query(None, description="状态筛选: unused/used/expired"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[PaginatedData[CouponResponse]]:
    """当前用户的优惠券列表"""
    result = await _service.list_coupons(
        user_id=current_user.id,
        page=page,
        page_size=page_size,
        status=status,
    )
    return success(data=result)


@router.post("/assign",
    response_model=APIResponse[CouponResponse],
    summary="领取优惠券",
    description="""
小程序 **优惠券** 页面使用。

**使用场景**: 用户通过优惠券码领取优惠券

**请求体**:
- `code`: 优惠券码

**响应**: 领取的优惠券信息

**认证**: 需登录
    """,
)
async def assign_coupon(
    body: CouponAssignRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[CouponResponse]:
    """下发优惠券"""
    result = await _service.assign_coupon(current_user.id, body)
    return success(data=result)


@router.post("/verify",
    response_model=APIResponse[CouponResponse],
    summary="核销优惠券",
    description="""
小程序 **下单/支付** 页面使用。

**使用场景**: 用户下单时核销优惠券，验证优惠券是否可用并计算抵扣金额

**请求体**:
- `coupon_id`: 优惠券 ID
- `order_amount`: 订单金额

**响应**: 核销结果，含抵扣金额

**认证**: 需登录
    """,
)
async def verify_coupon(
    body: CouponVerifyRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[CouponResponse]:
    """核销优惠券"""
    result = await _service.verify_coupon(current_user.id, body)
    return success(data=result)


@router.post("/validate", response_model=APIResponse[CouponResponse])
async def validate_coupon(
    body: CouponVerifyRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[CouponResponse]:
    """核销优惠券（validate 别名，兼容旧前端路径）"""
    result = await _service.verify_coupon(current_user.id, body)
    return success(data=result)