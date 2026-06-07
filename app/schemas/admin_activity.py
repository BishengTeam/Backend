from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.activity import ActivityResponse


class AdminActivityCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=256, description="活动标题")
    description: str | None = Field(None, description="活动描述")
    cover_url: str | None = Field(None, max_length=512, description="封面图片URL")
    location: str | None = Field(None, max_length=256, description="活动地点")
    start_time: datetime | None = Field(None, description="开始时间")
    end_time: datetime | None = Field(None, description="结束时间")
    max_participants: int = Field(0, ge=0, description="最大参与人数")
    is_active: bool = Field(True, description="是否上架")


class AdminActivityUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=256, description="活动标题")
    description: str | None = Field(None, description="活动描述")
    cover_url: str | None = Field(None, max_length=512, description="封面图片URL")
    location: str | None = Field(None, max_length=256, description="活动地点")
    start_time: datetime | None = Field(None, description="开始时间")
    end_time: datetime | None = Field(None, description="结束时间")
    max_participants: int | None = Field(None, ge=0, description="最大参与人数")
    is_active: bool | None = Field(None, description="是否上架")


class AdminActivityListItem(ActivityResponse):
    """管理端活动列表项 — 字段与 ActivityResponse 完全一致，通过继承复用"""