from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


CourseUploadKind = Literal["cover", "chapter_video"]


class CourseUploadCreate(BaseModel):
    kind: CourseUploadKind
    filename: str = Field(..., min_length=1, max_length=256)
    content_type: str = Field(..., min_length=3, max_length=128)
    size_bytes: int = Field(..., gt=0)
    course_id: int | None = Field(None, ge=1)
    title: str | None = Field(None, min_length=1, max_length=256)
    duration: int | None = Field(None, gt=0)
    sort_order: int | None = Field(None, ge=1)

    model_config = {"extra": "forbid"}


class CourseUploadPartRequest(BaseModel):
    part_number: int = Field(..., ge=1, le=10000)

    model_config = {"extra": "forbid"}


class CourseUploadComplete(BaseModel):
    model_config = {"extra": "forbid"}


class CourseUploadPart(BaseModel):
    part_number: int
    size_bytes: int
    etag: str


class CourseUploadResponse(BaseModel):
    id: int
    course_id: int | None
    kind: CourseUploadKind
    filename: str
    content_type: str
    size_bytes: int
    part_size: int
    status: Literal["pending", "completed", "bound", "aborted", "expired"]
    title: str | None
    duration: int | None
    sort_order: int | None
    object_key: str
    oss_upload_id: str
    upload_url: str | None = None
    expires_at: datetime
    completed_at: datetime | None = None
    parts: list[CourseUploadPart] = Field(default_factory=list)


class CourseUploadPartUrlResponse(BaseModel):
    upload_id: int
    part_number: int
    url: str
    expires_at: int


class CourseUploadCompleteResponse(BaseModel):
    upload: CourseUploadResponse
    object_key: str
