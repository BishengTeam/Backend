from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ReviewCreate(BaseModel):
    target_type: Literal["identity", "student", "enterprise", "order"] = Field(
        ..., description="审核对象类型"
    )
    target_id: int = Field(..., ge=1, description="审核对象 ID（user_id 或 order_id）")
    action: Literal["approve", "reject"] = Field(
        ..., description="审核动作"
    )
    comment: str | None = Field(None, max_length=1024, description="审核备注 / 驳回理由")


class ReviewResponse(BaseModel):
    id: int
    target_type: str
    target_id: int
    reviewer_id: int
    action: str
    comment: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReviewFilter(BaseModel):
    target_type: str | None = Field(None, description="按审核对象类型筛选")
    target_id: int | None = Field(None, ge=1, description="按审核对象 ID 筛选")
