from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

PlanStatus = Literal["draft", "published", "archived", "cancelled"]


class PlanCreate(BaseModel):
    """POST /admin/certifications/{code}/plans"""
    name: str = Field(..., min_length=1, max_length=128, description="批次名称")
    apply_start: datetime | None = Field(None, description="报名开始时间")
    apply_end: datetime | None = Field(None, description="报名截止时间")
    exam_date: datetime | None = Field(None, description="考试日期")
    capacity: int = Field(0, ge=0, description="总名额，0=不限")

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("批次名称不能为空")
        return value


class PlanUpdate(BaseModel):
    """PUT /admin/certifications/{code}/plans/{id} — 编辑草稿批次"""
    name: str | None = Field(None, min_length=1, max_length=128)
    apply_start: datetime | None = None
    apply_end: datetime | None = None
    exam_date: datetime | None = None
    capacity: int | None = Field(None, ge=0)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("批次名称不能为空")
        return value


class PlanResponse(BaseModel):
    """批次响应"""
    id: int
    product_type: str
    name: str
    apply_start: datetime | None = None
    apply_end: datetime | None = None
    exam_date: datetime | None = None
    capacity: int = 0
    enrolled: int = Field(0, description="当前占用名额的订单数，包含待支付、已支付和已完成")
    status: PlanStatus
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
