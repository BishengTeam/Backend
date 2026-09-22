from fastapi import APIRouter, Depends, Query

from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.points_mall import (
    MyCouponResponse,
    PointsMallItemCreate,
    PointsMallItemResponse,
    PointsMallItemUpdate,
    PointsMallRedeemRequest,
    UsableCouponQuery,
    UsableCouponResponse,
    UserPointsMallItemResponse,
)
from app.services.points_mall import PointsMallService

router = APIRouter(prefix="/points-mall", tags=["积分商城"])
_service = PointsMallService()


# ── User endpoints ──

@router.get("/items", response_model=APIResponse[list[UserPointsMallItemResponse]])
async def list_items(
    current_user: User = Depends(get_current_user),
) -> APIResponse[list[UserPointsMallItemResponse]]:
    return success(data=await _service.user_list_items(current_user.id))


@router.post("/redeem", response_model=APIResponse[MyCouponResponse])
async def redeem(
    body: PointsMallRedeemRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[MyCouponResponse]:
    return success(data=await _service.user_redeem(current_user.id, body.item_id))


@router.get("/my-coupons", response_model=APIResponse[list[MyCouponResponse]])
async def my_coupons(
    status: str | None = Query(None),
    current_user: User = Depends(get_current_user),
) -> APIResponse[list[MyCouponResponse]]:
    return success(data=await _service.user_my_coupons(current_user.id, status=status))


@router.post("/usable-coupons", response_model=APIResponse[list[UsableCouponResponse]])
async def usable_coupons(
    body: UsableCouponQuery,
    current_user: User = Depends(get_current_user),
) -> APIResponse[list[UsableCouponResponse]]:
    return success(
        data=await _service.user_usable_coupons(
            current_user.id,
            order_amount_cents=body.order_amount_cents,
            product_type=body.product_type,
            product_category=body.product_category,
        )
    )
