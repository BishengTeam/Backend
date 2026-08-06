from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class AdminSettingsUserCreate(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=12, max_length=128)
    role: Literal["admin"] = "admin"


class AdminSettingsUserUpdate(BaseModel):
    is_active: bool | None = None


class AdminSettingsPasswordReset(BaseModel):
    password: str = Field(..., min_length=12, max_length=128)


class AdminSettingsUserListItem(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
