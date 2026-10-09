from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class VideoWebUserLookupResponse(BaseModel):
    user_id: int
    phone: str
    is_active: bool


class VideoWebCodeCreate(BaseModel):
    course_id: int = Field(gt=0)
    quantity: int = Field(default=1, ge=1, le=500)
    note: str | None = Field(default=None, max_length=512)

    model_config = {"extra": "forbid"}


class VideoWebCodeItem(BaseModel):
    id: int
    code: str
    course_id: int
    status: str
    source_type: str | None = None
    source_order_id: int | None = None
    owner_miniapp_user_id: int | None = None
    redeemed_by: int | None = None
    redeemed_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime


class VideoWebCodeList(BaseModel):
    items: list[VideoWebCodeItem]
    total: int


class VideoWebCodeStatus(BaseModel):
    reason: str | None = Field(default=None, max_length=128)

    model_config = {"extra": "forbid"}
