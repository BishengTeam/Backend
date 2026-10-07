from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class AdminZoneCreate(BaseModel):
    zone_type: Literal["cert", "study", "activity", "employment", "training"]
    title: str = Field(..., min_length=1, max_length=256)
    cover_url: str | None = Field(None, max_length=512)
    description: str | None = None
    sort_order: int = 0
    is_active: bool = True


class AdminZoneUpdate(BaseModel):
    zone_type: Literal["cert", "study", "activity", "employment", "training"] | None = None
    title: str | None = Field(None, min_length=1, max_length=256)
    cover_url: str | None = Field(None, max_length=512)
    description: str | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class AdminZoneStatusToggle(BaseModel):
    is_active: bool


class AdminZoneSortItem(BaseModel):
    id: int
    sort_order: int


class AdminZoneListItem(BaseModel):
    id: int
    zone_type: str
    title: str
    cover_url: str | None = None
    description: str | None = None
    sort_order: int
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
