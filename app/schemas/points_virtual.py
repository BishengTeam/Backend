from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


PointsVirtualProductType = Literal["course", "quiz_library"]
PointsVirtualProductStatus = Literal["draft", "published", "offline"]


class PointsVirtualProductCreate(BaseModel):
    product_type: PointsVirtualProductType
    course_id: int | None = Field(None, gt=0)
    library_id: int | None = Field(None, gt=0)
    name: str = Field(min_length=1, max_length=128)
    cover_url: str | None = Field(None, max_length=512)
    description: str | None = Field(None, max_length=512)
    points_price: int = Field(gt=0)
    stock_limit: int | None = Field(None, ge=0)
    entitlement_days: int | None = Field(None, ge=1)
    available_from: datetime | None = None
    available_to: datetime | None = None
    status: PointsVirtualProductStatus = "draft"

    @model_validator(mode="after")
    def validate_content(self):
        if self.product_type == "course" and self.course_id is None:
            raise ValueError("课程商品必须关联课程")
        if self.product_type == "quiz_library" and self.library_id is None:
            raise ValueError("题库商品必须关联题库")
        if self.course_id is not None and self.library_id is not None:
            raise ValueError("商品只能关联一个内容对象")
        if self.available_from and self.available_to and self.available_from >= self.available_to:
            raise ValueError("可用开始时间必须早于结束时间")
        return self


class PointsVirtualProductUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    cover_url: str | None = Field(None, max_length=512)
    description: str | None = Field(None, max_length=512)
    points_price: int | None = Field(None, gt=0)
    stock_limit: int | None = Field(None, ge=0)
    entitlement_days: int | None = Field(None, ge=1)
    available_from: datetime | None = None
    available_to: datetime | None = None
    status: PointsVirtualProductStatus | None = None


class PointsVirtualProductResponse(BaseModel):
    id: int
    product_type: PointsVirtualProductType
    content_key: str
    course_id: int | None
    library_id: int | None
    name: str
    cover_url: str | None
    description: str | None
    points_price: int
    stock_limit: int | None
    remaining_stock: int | None = None
    total_redeemed: int
    entitlement_days: int | None
    available_from: datetime | None
    available_to: datetime | None
    status: PointsVirtualProductStatus
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PointsVirtualPrecheckRequest(BaseModel):
    product_id: int = Field(gt=0)
    idempotency_key: str | None = Field(None, min_length=8, max_length=64)


class PointsVirtualRedeemRequest(BaseModel):
    product_id: int = Field(gt=0)
    product_version: int = Field(gt=0)
    idempotency_key: str = Field(min_length=8, max_length=64)


class PointsVirtualOrderResponse(BaseModel):
    id: int
    order_no: str
    product_id: int
    product_type: PointsVirtualProductType
    product_name: str
    course_id: int | None
    library_id: int | None
    points_cost: int
    entitlement_days: int | None
    starts_at: datetime
    ends_at: datetime | None
    status: Literal["completed", "refunded"]
    completed_at: datetime
    refunded_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PointsVirtualPrecheckResponse(BaseModel):
    product: PointsVirtualProductResponse
    balance: int
    balance_after: int | None
    points_shortage: int | None
    can_redeem: bool
    blocked_reason: str | None
    already_owned: bool


class PointsVirtualRedeemResponse(PointsVirtualOrderResponse):
    balance: int
    idempotent: bool
    points_history_id: int | None = None


class PointsVirtualRefundRequest(BaseModel):
    reason_code: str = Field(min_length=1, max_length=64)
    reason_detail: str = Field(min_length=1, max_length=500)


class PointsVirtualRefundResponse(BaseModel):
    id: int
    order_id: int
    user_id: int
    points_returned: int
    reason_code: str
    reason_detail: str
    refunded_by_admin_id: int
    points_history_id: int
    refunded_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}
