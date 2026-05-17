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


class CourseDetailResponse(BaseModel):
    id: int
    title: str = Field(..., description="课程标题")
    category: str = Field(..., description="课程类目")
    description: str | None = Field(None, description="课程简介")
    cover_url: str | None = Field(None, description="封面图片 URL")
    video_url: str | None = Field(None, description="视频 URL")
    price: int = Field(..., description="价格，单位为分")
    batches: dict | None = Field(None, description="班次信息")
    teacher_name: str | None = Field(None, description="讲师名称")
    teacher_contact: str | None = Field(None, description="讲师联系方式")

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
