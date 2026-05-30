from datetime import datetime

from pydantic import BaseModel, Field


class AdminZoneCreate(BaseModel):
    zone_type: str = Field(..., min_length=1, max_length=32)
    title: str = Field(..., min_length=1, max_length=256)
    cover_url: str | None = Field(None, max_length=512)
    description: str | None = None
    link_url: str | None = Field(None, max_length=512)
    sort_order: int = 0
    is_active: bool = True


class AdminZoneUpdate(BaseModel):
    zone_type: str | None = Field(None, min_length=1, max_length=32)
    title: str | None = Field(None, min_length=1, max_length=256)
    cover_url: str | None = Field(None, max_length=512)
    description: str | None = None
    link_url: str | None = Field(None, max_length=512)
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
    link_url: str | None = None
    sort_order: int
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
