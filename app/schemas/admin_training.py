from datetime import datetime

from pydantic import BaseModel, Field


class AdminTrainingCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=256)
    description: str | None = None
    cover_url: str | None = Field(None, max_length=512)
    location: str | None = Field(None, max_length=256)
    start_time: datetime | None = None
    end_time: datetime | None = None
    max_participants: int = Field(0, ge=0)
    cert_type: str | None = Field(None, max_length=64)
    price: int = Field(0, ge=0)
    is_active: bool = True


class AdminTrainingUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=256)
    description: str | None = None
    cover_url: str | None = Field(None, max_length=512)
    location: str | None = Field(None, max_length=256)
    start_time: datetime | None = None
    end_time: datetime | None = None
    max_participants: int | None = Field(None, ge=0)
    cert_type: str | None = Field(None, max_length=64)
    price: int | None = Field(None, ge=0)
    is_active: bool | None = None


class AdminTrainingListItem(BaseModel):
    id: int
    title: str
    cover_url: str | None = None
    location: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    max_participants: int
    cert_type: str | None = None
    price: int
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
