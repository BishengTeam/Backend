from datetime import datetime

from pydantic import BaseModel, Field


class AdminJobCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=256, description="岗位标题")
    company: str = Field(..., min_length=1, max_length=128, description="公司名称")
    location: str | None = Field(None, max_length=128, description="工作地点")
    salary_range: str | None = Field(None, max_length=64, description="薪资范围")
    description: str | None = Field(None, description="岗位描述")
    requirements: str | None = Field(None, description="任职要求")
    contact_info: str | None = Field(None, max_length=256, description="联系方式")
    is_active: bool = Field(True, description="是否上架")


class AdminJobUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=256, description="岗位标题")
    company: str | None = Field(None, min_length=1, max_length=128, description="公司名称")
    location: str | None = Field(None, max_length=128, description="工作地点")
    salary_range: str | None = Field(None, max_length=64, description="薪资范围")
    description: str | None = Field(None, description="岗位描述")
    requirements: str | None = Field(None, description="任职要求")
    contact_info: str | None = Field(None, max_length=256, description="联系方式")
    is_active: bool | None = Field(None, description="是否上架")


class AdminJobListItem(BaseModel):
    id: int
    title: str
    company: str
    location: str | None = None
    salary_range: str | None = None
    description: str | None = None
    requirements: str | None = None
    contact_info: str | None = None
    is_active: bool
    created_at: datetime
    model_config = {"from_attributes": True}