from datetime import datetime

from pydantic import BaseModel, Field


class AdminSettingsUserCreate(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)
    role: str = Field("content_editor", min_length=1, max_length=32)


class AdminSettingsUserUpdate(BaseModel):
    role: str | None = Field(None, min_length=1, max_length=32)
    is_active: bool | None = None


class AdminSettingsUserListItem(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
