from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


DiscountType = Literal["fixed", "percent"]
ScopeType = Literal["global", "category", "product"]
ValidityType = Literal["days", "fixed_date"]
RedemptionStatus = Literal["unused", "used", "expired"]


# ── Admin: create / update ──

class PointsMallItemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(None, max_length=256)
    discount_type: DiscountType
    discount_value: int = Field(gt=0)
    scope_type: ScopeType = "global"
    scope_value: str | None = Field(None, max_length=64)
    min_order_amount_cents: int = Field(default=0, ge=0)
    points_cost: int = Field(gt=0)
    total_stock: int = Field(default=0, ge=0)
    per_user_limit: int = Field(default=1, ge=0)
    validity_type: ValidityType = "days"
    validity_days: int | None = Field(None, ge=1)
    valid_until: datetime | None = None
    sort_order: int = Field(default=0, ge=0)


class PointsMallItemUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    description: str | None = Field(None, max_length=256)
    discount_type: DiscountType | None = None
    discount_value: int | None = Field(None, gt=0)
    scope_type: ScopeType | None = None
    scope_value: str | None = Field(None, max_length=64)
    min_order_amount_cents: int | None = Field(None, ge=0)
    points_cost: int | None = Field(None, gt=0)
    total_stock: int | None = Field(None, ge=0)
    per_user_limit: int | None = Field(None, ge=0)
    validity_type: ValidityType | None = None
    validity_days: int | None = Field(None, ge=1)
    valid_until: datetime | None = None
    is_active: bool | None = None
    sort_order: int | None = Field(None, ge=0)


# ── Admin: response ──

class PointsMallItemResponse(BaseModel):
    id: int
    name: str
    description: str | None
    discount_type: DiscountType
    discount_value: int
    scope_type: ScopeType
    scope_value: str | None
    min_order_amount_cents: int
    points_cost: int
    total_stock: int
    total_redeemed: int
    remaining_stock: int
    per_user_limit: int
    validity_type: ValidityType
    validity_days: int | None
    valid_until: datetime | None
    is_active: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── User: mall list ──

class UserPointsMallItemResponse(BaseModel):
    id: int
    name: str
    description: str | None
    discount_type: DiscountType
    discount_value: int
    discount_label: str
    scope_label: str
    min_order_amount_cents: int
    points_cost: int
    remaining_stock: int
    per_user_limit: int
    user_redeemed_count: int
    can_redeem: bool
    redeem_blocked_reason: str | None


# ── User: redeem ──

class PointsMallRedeemRequest(BaseModel):
    item_id: int


class MyCouponResponse(BaseModel):
    id: int
    coupon_code: str
    name: str
    discount_type: DiscountType
    discount_value: int
    discount_label: str
    scope_type: ScopeType
    scope_value: str | None
    scope_label: str
    min_order_amount_cents: int
    status: RedemptionStatus
    expires_at: datetime
    used_at: datetime | None
    order_id: int | None
    created_at: datetime


# ── User: usable coupons for order ──

class UsableCouponQuery(BaseModel):
    order_amount_cents: int = Field(ge=0)
    product_type: str = Field(min_length=1, max_length=64)
    product_category: str | None = Field(None, max_length=64)


class UsableCouponResponse(MyCouponResponse):
    discounted_price_cents: int
    discount_amount_cents: int
