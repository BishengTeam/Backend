from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

PlanStatus = Literal[
    "draft",
    "published",
    "registration_closed",
    "finalized",
    "archived",
    "cancelled",
]


class PlanCreate(BaseModel):
    """POST /admin/certifications/{code}/plans"""
    name: str = Field(..., min_length=1, max_length=128, description="批次名称")
    apply_start: datetime | None = Field(None, description="报名开始时间")
    apply_end: datetime | None = Field(None, description="报名截止时间")
    exam_date: datetime | None = Field(None, description="考试日期")
    capacity: int = Field(0, ge=0, description="总名额，0=不限")
    price_cents: int = Field(50000, ge=1, description="报名价格，单位分")
    exam_location: str | None = Field(None, max_length=256, description="考试地点")
    description: str | None = Field(None, max_length=5000, description="报名说明")
    contact_name: str | None = Field(None, max_length=64, description="联系人")
    contact_phone: str | None = Field(None, max_length=20, description="联系电话")
    sort_order: int = Field(0, ge=0, description="排序值")

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
    price_cents: int | None = Field(None, ge=1)
    exam_location: str | None = Field(None, max_length=256)
    description: str | None = Field(None, max_length=5000)
    contact_name: str | None = Field(None, max_length=64)
    contact_phone: str | None = Field(None, max_length=20)
    sort_order: int | None = Field(None, ge=0)

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
    occupation_name: str = "信息安全管理员"
    occupation_code: str = "4-04-04-02-网络与信息安全管理员-信息安全管理员-中级工"
    skill_level: str = "中级工"
    exam_type: str = "新考"
    application_type: str = "学历型"
    price_cents: int = 50000
    exam_location: str | None = None
    description: str | None = None
    contact_name: str | None = None
    contact_phone: str | None = None
    sort_order: int = 0
    published_at: datetime | None = None
    registration_closed_at: datetime | None = None
    cancelled_at: datetime | None = None
    finalized_at: datetime | None = None
    cleanup_due_at: datetime | None = None
    enrolled: int = Field(0, description="当前占用名额的订单数，包含待支付、已支付和已完成")
    status: PlanStatus
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
