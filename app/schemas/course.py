from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

EnrollmentStatus = Literal[
    "pending_payment",
    "enrolled",
    "completed",
    "refunded",
    "cancelled",
    "expired",
]


class CourseListResponse(BaseModel):
    id: int
    title: str = Field(..., description="课程标题")
    category: str = Field(..., description="课程类目")
    description: str | None = Field(None, description="课程简介")
    cover_url: str | None = Field(None, description="封面图片 URL")
    price: int = Field(..., description="价格，单位为分")
    teacher_name: str | None = Field(None, description="讲师名称")

    model_config = {"from_attributes": True}


class ChapterResponse(BaseModel):
    """章节响应"""
    id: int
    title: str
    video_url: str | None
    duration: int | None
    sort_order: int

    model_config = {"from_attributes": True}


class ChapterCreate(BaseModel):
    """新增章节"""
    title: str = Field(..., max_length=256)
    video_url: str | None = None
    duration: int | None = None
    sort_order: int | None = None


class ChapterUpdate(BaseModel):
    """编辑章节（全部可选）"""
    title: str | None = Field(None, max_length=256)
    video_url: str | None = None
    duration: int | None = None
    sort_order: int | None = None


class ChapterSortItem(BaseModel):
    """排序项"""
    id: int
    sort_order: int


class ChapterProgressResponse(BaseModel):
    """学习进度响应"""
    last_chapter_id: int | None = None
    last_position_seconds: int = 0
    completed_chapter_ids: list[int] = Field(default_factory=list)


class CourseChaptersResponse(BaseModel):
    """课程章节、试看配置与当前用户进度。"""

    free_preview_seconds: int | None = None
    chapters: list[ChapterResponse] = Field(default_factory=list)
    progress: ChapterProgressResponse | None = None


class ChapterProgressUpsert(BaseModel):
    """上报学习进度"""
    chapter_id: int
    last_position_seconds: int = Field(..., ge=0)
    is_completed: bool = False


class CourseSchedule(BaseModel):
    """课程的一次具体上课安排。"""

    class_date: str = Field(
        ...,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="上课日期，格式 YYYY-MM-DD",
    )
    start_time: str = Field(
        ...,
        pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$",
        description="开始时间，格式 HH:mm",
    )
    end_time: str = Field(
        ...,
        pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$",
        description="结束时间，格式 HH:mm",
    )
    location: str | None = Field(None, max_length=128, description="上课地点")

    model_config = {"extra": "forbid"}


class CourseQuizLibrarySummary(BaseModel):
    """Current display data for a quiz library included with the course."""

    id: int
    library_code: str
    name: str
    description: str | None = None
    cover_url: str | None = None
    status: Literal["draft", "published", "suspended"]
    available: bool = Field(
        description="题库当前是否已发布并开放 V2 用户入口"
    )


class CourseDetailResponse(BaseModel):
    id: int
    title: str = Field(..., description="课程标题")
    category: str = Field(..., description="课程类目")
    description: str | None = Field(None, description="课程简介")
    cover_url: str | None = Field(None, description="封面图片 URL")
    price: int = Field(..., description="价格，单位为分")
    batches: dict[str, CourseSchedule] | None = Field(None, description="上课安排")
    teacher_name: str | None = Field(None, description="讲师名称")
    has_access: bool = Field(False, description="当前用户是否有学习权限")
    enrollment_id: int | None = Field(None, description="报名记录 ID（已报名时返回）")
    chapters: list[ChapterResponse] = Field(default_factory=list)
    free_preview_seconds: int | None = Field(None)
    included_quiz_libraries: list[CourseQuizLibrarySummary] = Field(
        default_factory=list,
        description="当前有效绑定的课程赠送题库展示资料",
    )

    @field_validator("batches", mode="before")
    @classmethod
    def _normalize_batches(cls, v):
        """历史数据可能把空班次存成 []，统一转成 {} 避免 Pydantic 校验失败。"""
        if v == []:
            return {}
        return v

    model_config = {"from_attributes": True}


class CourseFilter(BaseModel):
    category: str | None = Field(None, description="按类目筛选")


class CourseEnrollRequest(BaseModel):
    course_id: int = Field(..., gt=0)
    batch: str | None = Field(None, max_length=64)


class CoursePurchaseRequest(BaseModel):
    batch: str | None = Field(None, max_length=64, description="所选班次")


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
    order_id: int | None = Field(None, description="关联订单 ID")
    batch_selected: str | None = Field(None, description="所选班次")
    status: EnrollmentStatus = Field(..., description="报名状态")
    learning_access: bool = Field(..., description="是否有学习权限")
    access_granted_at: datetime | None = Field(None, description="权限开通时间")
    access_revoked_at: datetime | None = Field(None, description="权限撤销时间")
    created_at: datetime

    model_config = {"from_attributes": True}


class CourseAssetResponse(BaseModel):
    id: int
    course_id: int
    title: str
    asset_type: str
    sort_order: int
    is_preview: bool
    content_url: str


class CourseContentResponse(BaseModel):
    course_id: int
    title: str
    learning_access: bool
    assets: list[CourseAssetResponse]


class CourseAssetPlaybackResponse(BaseModel):
    asset_id: int
    url: str
    expires_at: int = Field(..., description="签名播放地址过期时间，Unix 秒")
