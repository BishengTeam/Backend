from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AdminCourseCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=256)
    category: str = Field(..., min_length=1, max_length=64)
    description: str | None = None
    cover_url: str | None = Field(None, max_length=512)
    video_url: str | None = Field(None, max_length=512)
    price: int = Field(..., ge=0)
    batches: Any | None = None
    teacher_name: str | None = Field(None, max_length=64)
    teacher_contact: str | None = Field(None, max_length=128)
    is_active: bool = True
    free_preview_seconds: int | None = Field(None, description="试看时长（秒），null=无试看")


class AdminCourseUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=256)
    category: str | None = Field(None, min_length=1, max_length=64)
    description: str | None = None
    cover_url: str | None = Field(None, max_length=512)
    video_url: str | None = Field(None, max_length=512)
    price: int | None = Field(None, ge=0)
    batches: Any | None = None
    teacher_name: str | None = Field(None, max_length=64)
    teacher_contact: str | None = Field(None, max_length=128)
    is_active: bool | None = None
    free_preview_seconds: int | None = Field(None, description="试看时长（秒），null=无试看")


class AdminCourseListItem(BaseModel):
    id: int
    title: str
    category: str
    description: str | None = None
    cover_url: str | None = None
    video_url: str | None = None
    price: int
    batches: Any | None = None
    teacher_name: str | None = None
    teacher_contact: str | None = None
    is_active: bool
    free_preview_seconds: int | None = Field(None, description="试看时长（秒），null=无试看")
    created_at: datetime

    model_config = {"from_attributes": True}
