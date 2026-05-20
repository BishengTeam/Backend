from datetime import datetime

from pydantic import BaseModel, Field


class AdminCouponCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)
    type: str = Field("fixed", min_length=1, max_length=16)
    value: int = Field(..., ge=0)
    min_order_amount: int = Field(0, ge=0)
    valid_from: datetime | None = None
    valid_to: datetime | None = None


class AdminCouponBatchCreate(BaseModel):
    code_prefix: str = Field(..., min_length=1, max_length=48)
    count: int = Field(..., ge=1, le=1000)
    type: str = Field("fixed", min_length=1, max_length=16)
    value: int = Field(..., ge=0)
    min_order_amount: int = Field(0, ge=0)
    valid_from: datetime | None = None
    valid_to: datetime | None = None


class AdminCouponListItem(BaseModel):
    id: int
    code: str
    type: str
    value: int
    min_order_amount: int
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
