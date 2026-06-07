from datetime import datetime

from pydantic import BaseModel, Field


class AdminJobCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=256)
    company: str = Field(..., min_length=1, max_length=128)
    location: str | None = Field(None, max_length=128)
    salary_range: str | None = Field(None, max_length=64)
    description: str | None = None
    requirements: str | None = None
    contact_info: str | None = Field(None, max_length=256)
    is_active: bool = True


class AdminJobUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=256)
    company: str | None = Field(None, min_length=1, max_length=128)
    location: str | None = Field(None, max_length=128)
    salary_range: str | None = Field(None, max_length=64)
    description: str | None = None
    requirements: str | None = None
    contact_info: str | None = Field(None, max_length=256)
    is_active: bool | None = None


class AdminJobListItem(BaseModel):
    id: int
    title: str
    company: str
    location: str | None = None
    salary_range: str | None = None
    description: str | None = None
    requirements: str | None = None
    contact_info: str | None = None
    is_active: bool
    created_at: datetime
    model_config = {"from_attributes": True}
