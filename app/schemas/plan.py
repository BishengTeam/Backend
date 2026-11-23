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
PlanImpactAction = Literal["cancel", "finalize"]


class PlanCreate(BaseModel):
    """POST /admin/certifications/{code}/plans"""
    name: str = Field(..., min_length=1, max_length=128, description="批次名称")
    apply_start: datetime | None = Field(None, description="报名开始时间")
    apply_end: datetime | None = Field(None, description="报名截止时间")
    exam_date: datetime | None = Field(None, description="考试日期")
    capacity: int = Field(0, ge=0, description="总名额，0=不限")
    price_cents: int = Field(50000, ge=1, description="报名价格，单位分")
    exam_location: str | None = Field(None, max_length=256, description="考试地点")
    exam_org_name: str | None = Field(None, max_length=128, description="考试机构")
    exam_org_code: str | None = Field(None, max_length=64, description="考试机构代码")
    exam_date_note: str | None = Field(None, max_length=64, description="考试时间备注")
    confirm_holiday: bool = Field(False, description="确认为假期考试（人社批次）")
    projects: list["PlanProjectInput"] | None = None
    description: str | None = Field(None, max_length=5000, description="报名说明")
    registration_notice: str | None = Field(
        None, max_length=5000, description="报名注意事项（退改规则等）"
    )
    contact_name: str | None = Field(None, max_length=64, description="联系人")
    contact_phone: str | None = Field(None, max_length=20, description="联系电话")
    sort_order: int = Field(0, ge=0, description="排序值")
    program_code: str | None = Field(
        None,
        min_length=1,
        max_length=64,
        description="人社受控报考组合编码；其他认证类型不能传该字段",
    )
    project_config_id: int | None = Field(None, ge=1)
    exam_type: str | None = Field(None, max_length=16)
    application_type: str | None = Field(None, max_length=16)

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
    exam_org_name: str | None = Field(None, max_length=128)
    exam_org_code: str | None = Field(None, max_length=64)
    exam_date_note: str | None = Field(None, max_length=64)
    confirm_holiday: bool | None = None
    projects: list["PlanProjectInput"] | None = None
    description: str | None = Field(None, max_length=5000)
    registration_notice: str | None = Field(None, max_length=5000)
    contact_name: str | None = Field(None, max_length=64)
    contact_phone: str | None = Field(None, max_length=20)
    sort_order: int | None = Field(None, ge=0)
    program_code: str | None = Field(None, min_length=1, max_length=64)
    project_code: str | None = Field(None, max_length=32)
    project_config_id: int | None = Field(None, ge=1)
    exam_type: str | None = Field(None, max_length=16)
    application_type: str | None = Field(None, max_length=16)

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
    product_type: str | None = None
    project_name: str | None = None
    renshe_config_id: int | None = None
    renshe_config: dict | None = None
    name: str
    apply_start: datetime | None = None
    apply_end: datetime | None = None
    exam_date: datetime | None = None
    capacity: int = 0
    occupation_name: str | None = None
    occupation_code: str | None = None
    skill_level: str | None = None
    exam_type: str | None = None
    application_type: str | None = None
    price_cents: int = 50000
    exam_location: str | None = None
    exam_org_name: str | None = None
    exam_org_code: str | None = None
    exam_date_note: str | None = None
    description: str | None = None
    registration_notice: str | None = None
    contact_name: str | None = None
    contact_phone: str | None = None
    sort_order: int = 0
    published_at: datetime | None = None
    registration_closed_at: datetime | None = None
    cancelled_at: datetime | None = None
    finalized_at: datetime | None = None
    cleanup_due_at: datetime | None = None
    enrolled: int = Field(0, description="当前占用名额的订单数，包含待支付、已支付和已完成")
    projects: list["PlanProjectResponse"] = Field(default_factory=list)
    status: PlanStatus
    created_at: datetime

    model_config = {"from_attributes": True}


class PlanProjectInput(BaseModel):
    product_id: int = Field(..., ge=1)
    price_cents: int = Field(..., ge=0)
    sort_order: int = Field(0, ge=0)


class PlanProjectResponse(BaseModel):
    id: int
    product_id: int
    product_code: str
    product_name: str
    product_is_active: bool = True
    product_renshe_status: str | None = None
    skill_level: str | None = None
    config_id: int | None = None
    config: dict | None = None
    price_cents: int
    sort_order: int
    is_active: bool
    updated_at: datetime

    model_config = {"from_attributes": True}


class PlanImpactBlocker(BaseModel):
    """A machine-readable reason why the requested batch action cannot run."""

    code: Literal["plan_status", "pending_applications"]
    message: str
    count: int = Field(0, ge=0)
    status_counts: dict[str, int] = Field(default_factory=dict)


class PlanImpactResponse(BaseModel):
    """Read-only snapshot used by the admin dangerous-action confirmation UI."""

    action: PlanImpactAction
    plan_id: int
    plan_name: str
    plan_status: PlanStatus
    affected_application_count: int = Field(..., ge=0)
    pending_order_close_count: int = Field(..., ge=0)
    refund_candidate_count: int = Field(..., ge=0)
    refund_amount_cents: int = Field(..., ge=0)
    blocking_application_count: int = Field(..., ge=0)
    blocking_status_counts: dict[str, int] = Field(default_factory=dict)
    previewed_at: datetime
    cleanup_due_at: datetime
    can_execute: bool
    blockers: list[PlanImpactBlocker] = Field(default_factory=list)


PlanCreate.model_rebuild()
PlanUpdate.model_rebuild()
PlanResponse.model_rebuild()
