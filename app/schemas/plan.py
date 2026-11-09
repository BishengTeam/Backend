"""认证批次 Schema v2：跨厂商批次 + 产品关联 + 时段。"""
from datetime import datetime, time
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


# ============================================================
# 时间段
# ============================================================

class TimeSegmentCreate(BaseModel):
    label: str = Field(..., min_length=1, max_length=32, description='时段标签，如"上午场"')
    start_time: time = Field(..., description="开始时间，如 09:00")
    end_time: time = Field(..., description="结束时间，如 12:00")
    sort_order: int = Field(0, ge=0)


class TimeSegmentUpdate(BaseModel):
    label: str | None = Field(None, min_length=1, max_length=32)
    start_time: time | None = None
    end_time: time | None = None
    sort_order: int | None = Field(None, ge=0)


class TimeSegmentResponse(BaseModel):
    id: int
    label: str
    start_time: time
    end_time: time
    sort_order: int

    model_config = {"from_attributes": True}


# ============================================================
# 批次×产品
# ============================================================

class PlanProductCreate(BaseModel):
    """添加产品到批次。"""
    product_id: int = Field(..., ge=1, description="cert_product.id")
    capacity: int = Field(0, ge=0, description="该产品在此批次的名额，0=不限")

    # 人社专属（产品 type='renshe' 时必填）
    renshe_config_id: int | None = Field(None, ge=1, description="人社项目配置 ID")
    occupation_name: str | None = Field(None, max_length=64)
    occupation_code: str | None = Field(None, max_length=160)
    skill_level: str | None = Field(None, max_length=32)
    exam_type: str | None = Field(None, max_length=16)
    application_type: str | None = Field(None, max_length=16)


class PlanProductUpdate(BaseModel):
    capacity: int | None = Field(None, ge=0)
    renshe_config_id: int | None = Field(None, ge=1)
    occupation_name: str | None = Field(None, max_length=64)
    occupation_code: str | None = Field(None, max_length=160)
    skill_level: str | None = Field(None, max_length=32)
    exam_type: str | None = Field(None, max_length=16)
    application_type: str | None = Field(None, max_length=16)


class PlanProductResponse(BaseModel):
    id: int
    plan_id: int
    product_id: int
    product_code: str
    product_name: str
    product_type: str
    capacity: int
    enrolled: int = 0
    remaining: int | None = None
    # 人社
    renshe_config_id: int | None = None
    occupation_name: str | None = None
    occupation_code: str | None = None
    skill_level: str | None = None
    exam_type: str | None = None
    application_type: str | None = None
    renshe_config: dict | None = None  # v2: 配置快照（仅人社产品有值）


# ============================================================
# 批次 CRUD
# ============================================================

class BatchCreate(BaseModel):
    """POST /admin/certifications/batches"""
    name: str = Field(..., min_length=1, max_length=128, description="批次名称（全局唯一）")
    apply_start: datetime | None = Field(None, description="报名开始时间")
    apply_end: datetime | None = Field(None, description="报名截止时间")
    exam_date: datetime | None = Field(None, description="考试日期")
    exam_location: str | None = Field(None, max_length=256)
    description: str | None = Field(None, max_length=5000)
    contact_name: str | None = Field(None, max_length=64)
    contact_phone: str | None = Field(None, max_length=20)
    sort_order: int = Field(0, ge=0)
    # 可选：创建时直接带产品和时段
    products: list[PlanProductCreate] = Field(default_factory=list)
    time_segments: list[TimeSegmentCreate] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("批次名称不能为空")
        return value


class BatchUpdate(BaseModel):
    """PUT /admin/certifications/batches/{id}（仅 draft）"""
    name: str | None = Field(None, min_length=1, max_length=128)
    apply_start: datetime | None = None
    apply_end: datetime | None = None
    exam_date: datetime | None = None
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


class BatchResponse(BaseModel):
    """批次完整响应。"""
    id: int
    name: str
    apply_start: datetime | None = None
    apply_end: datetime | None = None
    exam_date: datetime | None = None
    exam_location: str | None = None
    description: str | None = None
    contact_name: str | None = None
    contact_phone: str | None = None
    sort_order: int = 0
    status: PlanStatus
    published_at: datetime | None = None
    registration_closed_at: datetime | None = None
    cancelled_at: datetime | None = None
    finalized_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # 聚合
    products: list[PlanProductResponse] = []
    time_segments: list[TimeSegmentResponse] = []
    total_enrolled: int = 0


class BatchListResponse(BaseModel):
    """批次列表项（精简）。"""
    id: int
    name: str
    status: PlanStatus
    apply_start: datetime | None = None
    apply_end: datetime | None = None
    exam_date: datetime | None = None
    product_count: int = 0
    total_enrolled: int = 0


# ============================================================
# 兼容旧名（过渡期引用）
# ============================================================

PlanCreate = BatchCreate
PlanUpdate = BatchUpdate
PlanResponse = BatchResponse


class PlanImpactResponse(BaseModel):
    plan_id: int
    action: PlanImpactAction
    affected_orders: int
    message: str


# ============================================================
# 影响分析（保留旧结构）
# ============================================================

class PlanImpactBlocker(BaseModel):
    """阻断批次取消/终审的原因。"""
    kind: str
    order_id: int | None = None
    message: str
