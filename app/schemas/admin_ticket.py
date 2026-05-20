from datetime import datetime

from pydantic import BaseModel, Field


class AdminTicketFilter(BaseModel):
    status: str | None = Field(None, max_length=32)


class AdminTicketUpdate(BaseModel):
    teacher_id: int | None = Field(None, ge=1)
    status: str | None = None


class AdminTicketListItem(BaseModel):
    id: int
    user_id: int
    teacher_id: int | None = None
    content: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
