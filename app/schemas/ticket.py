from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TicketCreateRequest(BaseModel):
    content: str = Field(..., min_length=1, description="工单内容")


class TicketListItem(BaseModel):
    id: int
    content: str | None = Field(None, description="工单内容（截断50字）")
    status: str = Field(..., description="工单状态")
    created_at: datetime

    model_config = {"from_attributes": True}


class TicketDetail(BaseModel):
    id: int
    content: str | None = Field(None, description="工单内容")
    status: str = Field(..., description="工单状态")
    teacher_id: int | None = Field(None, description="处理教师 ID")
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
