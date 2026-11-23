from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


TicketBatchStatus = Literal["waiting_manual", "processing", "resolved"]


class AdminTicketFilter(BaseModel):
    status: str | None = Field(None, max_length=32)


class AdminTicketUpdate(BaseModel):
    teacher_id: int | None = Field(None, ge=1)
    status: str | None = None


class AdminTicketBatchStatusUpdate(BaseModel):
    """批量改状态：一次最多 200 条，空列表直接 422。"""

    ids: list[int] = Field(..., min_length=1, max_length=200)
    status: TicketBatchStatus


class AdminTicketBatchStatusResult(BaseModel):
    updated: int
    skipped: int


class AdminTicketListItem(BaseModel):
    id: int
    user_id: int
    teacher_id: int | None = None
    content: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
