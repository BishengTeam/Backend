from datetime import datetime

from pydantic import BaseModel, Field


class CouponResponse(BaseModel):
    id: int
    code: str
    type: str
    value: int
    min_order_amount: int
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    status: str  # from user_coupon.status
    used_at: datetime | None = None


class CouponAssignRequest(BaseModel):
    coupon_code: str = Field(..., min_length=1, max_length=64, description="优惠券码")


class CouponVerifyRequest(BaseModel):
    coupon_code: str = Field(..., min_length=1, max_length=64, description="优惠券码")
