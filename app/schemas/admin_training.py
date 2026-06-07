from datetime import datetime

from pydantic import BaseModel, Field


class AdminTrainingCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=256, description="培训标题")
    description: str | None = Field(None, description="培训描述")
    cover_url: str | None = Field(None, max_length=512, description="封面图片URL")
    location: str | None = Field(None, max_length=256, description="培训地点")
    start_time: datetime | None = Field(None, description="开始时间")
    end_time: datetime | None = Field(None, description="结束时间")
    max_participants: int = Field(0, ge=0, description="最大参与人数")
    cert_type: str | None = Field(None, max_length=64, description="关联认证类型")
    price: int = Field(0, ge=0, description="培训费用（分）")
    is_active: bool = Field(True, description="是否上架")


class AdminTrainingUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=256, description="培训标题")
    description: str | None = Field(None, description="培训描述")
    cover_url: str | None = Field(None, max_length=512, description="封面图片URL")
    location: str | None = Field(None, max_length=256, description="培训地点")
    start_time: datetime | None = Field(None, description="开始时间")
    end_time: datetime | None = Field(None, description="结束时间")
    max_participants: int | None = Field(None, ge=0, description="最大参与人数")
    cert_type: str | None = Field(None, max_length=64, description="关联认证类型")
    price: int | None = Field(None, ge=0, description="培训费用（分）")
    is_active: bool | None = Field(None, description="是否上架")


class AdminTrainingListItem(BaseModel):
    id: int
    title: str
    description: str | None = None
    cover_url: str | None = None
    location: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    max_participants: int
    cert_type: str | None = None
    price: int
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


TrainingListItem = AdminTrainingListItem
# TrainingListItem 是 AdminTrainingListItem 的别名，供用户端和聚合端使用。
# 消除「Admin* Schema 被用户端接口引用」的命名不对称。