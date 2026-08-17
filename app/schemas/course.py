from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


EnrollmentStatus = Literal[
    "pending_payment",
    "enrolled",
    "completed",
    "refunded",
    "cancelled",
    "expired",
]
CourseStatus = Literal["draft", "published", "offline", "archived"]
VideoSourceType = Literal["external_url", "private_object"]


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
    id: int
    title: str
    video_url: str | None = None
    video_source_type: VideoSourceType = "external_url"
    video_storage_key: str | None = None
    duration: int | None = None
    sort_order: int
    is_preview: bool = False

    model_config = {"from_attributes": True}


class ChapterCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=256)
    video_source_type: VideoSourceType = "external_url"
    video_url: str | None = Field(None, max_length=512)
    video_storage_key: str | None = Field(None, max_length=512)
    duration: int | None = Field(None, ge=0)
    sort_order: int | None = Field(None, ge=0)
    is_preview: bool = False

    @model_validator(mode="after")
    def validate_video_source(self) -> "ChapterCreate":
        if self.video_source_type == "external_url" and not self.video_url:
            raise ValueError("外部视频章节必须提供 video_url")
        if self.video_source_type == "private_object" and not self.video_storage_key:
            raise ValueError("私有视频章节必须提供 video_storage_key")
        return self


class ChapterUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=256)
    video_source_type: VideoSourceType | None = None
    video_url: str | None = Field(None, max_length=512)
    video_storage_key: str | None = Field(None, max_length=512)
    duration: int | None = Field(None, ge=0)
    sort_order: int | None = Field(None, ge=0)
    is_preview: bool | None = None

    @model_validator(mode="after")
    def validate_video_source(self) -> "ChapterUpdate":
        source = self.video_source_type
        if source == "external_url" and self.video_url is not None and not self.video_url:
            raise ValueError("外部视频地址不能为空")
        if source == "private_object" and self.video_storage_key is not None and not self.video_storage_key:
            raise ValueError("私有视频对象键不能为空")
        return self


class ChapterSortItem(BaseModel):
    id: int
    sort_order: int = Field(..., ge=0)


class ChapterProgressResponse(BaseModel):
    last_chapter_id: int | None = None
    last_position_seconds: int = 0
    completed_chapter_ids: list[int] = Field(default_factory=list)


class CourseChaptersResponse(BaseModel):
    free_preview_seconds: int | None = None
    chapters: list[ChapterResponse] = Field(default_factory=list)
    progress: ChapterProgressResponse | None = None


class ChapterProgressUpsert(BaseModel):
    chapter_id: int
    last_position_seconds: int = Field(..., ge=0)
    is_completed: bool = False


class CourseQuizModuleSummary(BaseModel):
    id: int
    name: str
    description: str | None = None
    status: str


class CourseQuizLibrarySummary(BaseModel):
    id: int
    library_code: str
    name: str
    description: str | None = None
    cover_url: str | None = None
    status: Literal["draft", "published", "suspended"]
    available: bool = Field(description="题库当前是否已发布并开放 V2 用户入口")
    modules: list[CourseQuizModuleSummary] = Field(default_factory=list)


class CourseDetailResponse(BaseModel):
    id: int
    title: str = Field(..., description="课程标题")
    category: str = Field(..., description="课程类目")
    description: str | None = Field(None, description="课程简介")
    cover_url: str | None = Field(None, description="封面图片 URL")
    price: int = Field(..., description="价格，单位为分")
    teacher_name: str | None = Field(None, description="讲师名称")
    status: CourseStatus
    has_access: bool = Field(False, description="当前用户是否有学习权限")
    enrollment_id: int | None = Field(None, description="报名记录 ID（已报名时返回）")
    chapters: list[ChapterResponse] = Field(default_factory=list)
    free_preview_seconds: int | None = Field(None)
    included_quiz_libraries: list[CourseQuizLibrarySummary] = Field(
        default_factory=list,
        description="当前有效绑定的课程赠送题库展示资料和模块目录",
    )

    model_config = {"from_attributes": True}


class CourseFilter(BaseModel):
    category: str | None = Field(None, description="按类目筛选")


class CourseEnrollRequest(BaseModel):
    course_id: int = Field(..., gt=0)


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
    order_id: int | None = Field(None, description="关联订单 ID")
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
