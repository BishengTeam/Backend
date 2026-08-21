from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


EnrollmentStatus = Literal[
    "pending_payment",
    "enrolled",
    "completed",
    "refunded",
    "cancelled",
    "expired",
]
CourseStatus = Literal["draft", "published", "offline", "archived"]


class CourseListResponse(BaseModel):
    id: int
    title: str
    category: str
    description: str | None = None
    cover_url: str = ""
    price: int = Field(description="价格，单位为分")
    price_yuan: Decimal = Field(default=Decimal("0.00"), description="价格，单位为元")
    teacher_name: str | None = None


class ChapterResponse(BaseModel):
    id: int
    title: str
    duration: int
    sort_order: int
    is_preview: bool
    can_play: bool


class ChapterProgressResponse(BaseModel):
    last_chapter_id: int | None = None
    last_position_seconds: int = 0
    completed_chapter_ids: list[int] = Field(default_factory=list)


class CourseChaptersResponse(BaseModel):
    preview_chapter_count: int
    chapters: list[ChapterResponse] = Field(default_factory=list)
    progress: ChapterProgressResponse | None = None


class ChapterProgressUpsert(BaseModel):
    chapter_id: int
    last_position_seconds: int = Field(..., ge=0)
    is_completed: bool = False

    model_config = {"extra": "forbid"}


class CourseQuizModuleSummary(BaseModel):
    id: int
    name: str
    description: str | None
    status: str


class CourseQuizLibrarySummary(BaseModel):
    id: int
    library_code: str
    name: str
    description: str | None
    cover_url: str | None
    status: Literal["draft", "published", "suspended"]
    available: bool
    modules: list[CourseQuizModuleSummary] = Field(default_factory=list)


class CourseDetailResponse(BaseModel):
    id: int
    title: str
    category: str
    description: str | None = None
    cover_url: str = ""
    price: int
    price_yuan: Decimal = Decimal("0.00")
    teacher_name: str | None = None
    status: CourseStatus = "draft"
    has_access: bool = False
    enrollment_id: int | None = None
    preview_chapter_count: int = 0
    chapter_count: int = 0
    chapters: list[ChapterResponse] = Field(default_factory=list)
    included_quiz_libraries: list[CourseQuizLibrarySummary] = Field(
        default_factory=list
    )


class CourseFilter(BaseModel):
    category: str | None = None


class CourseEnrollRequest(BaseModel):
    course_id: int = Field(..., gt=0)

    model_config = {"extra": "forbid"}


class CoursePurchaseRequest(BaseModel):
    model_config = {"extra": "forbid"}


class CoursePurchaseResponse(BaseModel):
    course_id: int
    enrollment_id: int
    payment_required: bool
    order_id: int | None = None
    status: EnrollmentStatus
    learning_access: bool


class CourseEnrollmentResponse(BaseModel):
    id: int
    course: CourseListResponse
    order_id: int | None = None
    status: EnrollmentStatus
    learning_access: bool
    access_granted_at: datetime | None = None
    access_revoked_at: datetime | None = None
    created_at: datetime


class ChapterPlaybackResponse(BaseModel):
    chapter_id: int
    url: str
    expires_at: int = Field(description="签名播放地址过期时间，Unix 秒")
