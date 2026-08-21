from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.course import CourseStatus, EnrollmentStatus


CourseLifecycleAction = Literal["publish", "offline", "archive", "restore"]
BindingStatusAction = Literal["active", "inactive"]


class AdminCourseCategoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    sort_order: int = Field(0, ge=0)
    is_active: bool = True


class AdminCourseCategoryUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=64)
    sort_order: int | None = Field(None, ge=0)
    is_active: bool | None = None


class AdminCourseCategoryResponse(BaseModel):
    id: int
    name: str
    sort_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AdminCourseCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=256)
    category: str = Field(..., min_length=1, max_length=64)
    description: str | None = None
    cover_upload_id: int = Field(..., gt=0)
    price_yuan: Decimal = Field(..., ge=0, decimal_places=2)
    teacher_name: str | None = Field(None, max_length=64)
    teacher_contact: str | None = Field(None, max_length=128)
    preview_chapter_count: int = Field(0, ge=0)
    model_config = {"extra": "forbid"}


class AdminCourseUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=256)
    category: str | None = Field(None, min_length=1, max_length=64)
    description: str | None = None
    cover_upload_id: int | None = Field(None, gt=0)
    teacher_name: str | None = Field(None, max_length=64)
    teacher_contact: str | None = Field(None, max_length=128)
    preview_chapter_count: int | None = Field(None, ge=0)
    model_config = {"extra": "forbid"}


class AdminCourseListItem(BaseModel):
    id: int
    title: str
    category: str
    description: str | None = None
    cover_url: str | None = None
    price: int
    price_yuan: Decimal
    teacher_name: str | None = None
    teacher_contact: str | None = None
    preview_chapter_count: int
    status: CourseStatus
    bound_quiz_library_count: int = 0
    enrollment_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AdminCoursePriceUpdate(BaseModel):
    price_yuan: Decimal = Field(..., ge=0, decimal_places=2)

    model_config = {"extra": "forbid"}


class AdminChapterUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=256)
    duration: int | None = Field(None, gt=0)
    sort_order: int | None = Field(None, ge=1)

    model_config = {"extra": "forbid"}


class AdminChapterSortItem(BaseModel):
    id: int = Field(..., gt=0)
    sort_order: int = Field(..., ge=1)


class AdminChapterReplaceVideo(BaseModel):
    upload_id: int = Field(..., gt=0)

    model_config = {"extra": "forbid"}


class AdminChapterBatchCreate(BaseModel):
    upload_ids: list[int] = Field(..., min_length=1, max_length=50)

    model_config = {"extra": "forbid"}


class AdminChapterResponse(BaseModel):
    id: int
    title: str
    video_storage_key: str
    original_filename: str
    content_type: str
    size_bytes: int
    duration: int
    sort_order: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AdminCourseLifecycleRequest(BaseModel):
    action: CourseLifecycleAction
    model_config = {"extra": "forbid"}


class AdminCourseEnrollmentListItem(BaseModel):
    id: int
    user_id: int
    course_id: int
    course_title: str
    order_id: int | None = None
    order_status: str | None = None
    order_price: int | None = None
    active_entitlement_count: int = 0
    entitlement_sources: list[str] = Field(default_factory=list)
    status: EnrollmentStatus
    learning_access: bool
    access_granted_at: datetime | None = None
    access_revoked_at: datetime | None = None
    created_at: datetime




class AdminCourseBindingCreate(BaseModel):
    library_id: int = Field(..., gt=0)
    backfill_confirmations: list[str] = Field(default_factory=list)
    model_config = {"extra": "forbid"}


class AdminCourseBindingStatusRequest(BaseModel):
    status: BindingStatusAction
    model_config = {"extra": "forbid"}


class AdminCourseBindingResponse(BaseModel):
    id: int
    course_id: int
    library_id: int
    library_name: str
    library_code: str
    status: Literal["active", "inactive"]
    lock_version: int
    created_at: datetime
    updated_at: datetime


class AdminCourseBindingImpact(BaseModel):
    course_id: int
    library_id: int
    course_status: CourseStatus
    library_status: str
    active_enrollment_count: int
    existing_entitlement_count: int
    candidates_to_backfill: int
    active_session_count: int
    other_active_source_count: int
    can_execute: bool
    blockers: list[str] = Field(default_factory=list)


class AdminCourseEntitlementJobItemResponse(BaseModel):
    id: int
    enrollment_id: int
    user_id: int
    status: Literal["pending", "succeeded", "failed"]
    error_message: str | None = None


class AdminCourseEntitlementJobResponse(BaseModel):
    id: int
    course_id: int
    library_id: int
    action: Literal["backfill", "revoke"]
    status: Literal["queued", "running", "succeeded", "partial", "failed"]
    batch_size: int
    total_count: int
    processed_count: int
    success_count: int
    failure_count: int
    retry_count: int
    last_error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    failed_items: list[AdminCourseEntitlementJobItemResponse] = Field(default_factory=list)


class AdminCourseAuditListItem(BaseModel):
    id: int
    actor_type: Literal["admin", "system", "user"]
    actor_id: int | None = None
    action: str
    object_type: str
    object_id: int | None = None
    result: Literal["succeeded", "failed"]
    changed_fields: dict | None = None
    summary: dict | None = None
    request_id: str | None = None
    ip_address: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AdminCourseChapterPage(BaseModel):
    items: list[AdminChapterResponse]
    total: int
    page: int
    page_size: int
