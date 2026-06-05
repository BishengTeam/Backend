from fastapi import APIRouter, Depends, Query

from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.coupon import CouponAssignRequest, CouponResponse, CouponVerifyRequest
from app.services.coupon import CouponService

router = APIRouter(prefix="/coupons", tags=["优惠券"])
_service = CouponService()


@router.get("", response_model=APIResponse[PaginatedData[CouponResponse]])
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


@router.post("/assign", response_model=APIResponse[CouponResponse])
async def assign_coupon(
    body: CouponAssignRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[CouponResponse]:
    """下发优惠券"""
    result = await _service.assign_coupon(current_user.id, body)
    return success(data=result)


@router.post("/verify", response_model=APIResponse[CouponResponse])
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