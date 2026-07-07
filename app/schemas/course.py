from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

EnrollmentStatus = Literal["enrolled", "completed", "expired"]


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


class ChapterProgressUpsert(BaseModel):
    """上报学习进度"""
    chapter_id: int
    last_position_seconds: int = Field(..., ge=0)
    is_completed: bool = False


class CourseDetailResponse(BaseModel):
    id: int
    title: str = Field(..., description="课程标题")
    category: str = Field(..., description="课程类目")
    description: str | None = Field(None, description="课程简介")
    cover_url: str | None = Field(None, description="封面图片 URL")
    video_url: str | None = Field(None, description="课程介绍/宣传视频 URL")
    price: int = Field(..., description="价格，单位为分")
    batches: dict | None = Field(None, description="班次信息")
    teacher_name: str | None = Field(None, description="讲师名称")
    teacher_contact: str | None = Field(None, description="讲师联系方式")
    has_access: bool = Field(False, description="当前用户是否有学习权限")
    enrollment_id: int | None = Field(None, description="报名记录 ID（已报名时返回）")
    chapters: list[ChapterResponse] = Field(default_factory=list)
    free_preview_seconds: int | None = Field(None)

    model_config = {"from_attributes": True}


class CourseFilter(BaseModel):
    category: str | None = Field(None, description="按类目筛选")


class CourseEnrollRequest(BaseModel):
    course_id: int
    batch: str | None = None


class CourseEnrollmentResponse(BaseModel):
    id: int
    course: CourseListResponse
    batch_selected: str | None = Field(None, description="所选班次")
    status: EnrollmentStatus = Field(..., description="报名状态：enrolled=已报名 / completed=已完成 / expired=已过期")
    learning_access: bool = Field(..., description="是否有学习权限")
    created_at: datetime

    model_config = {"from_attributes": True}
