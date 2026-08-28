from datetime import datetime

from pydantic import BaseModel, Field


VALID_TARGET_TYPES = ("cert", "course", "activity", "zone", "url")


class BannerCreate(BaseModel):
    image_url: str = Field(..., min_length=1, max_length=512, description="Banner 图片 URL")
    jump_link: str | None = Field(None, max_length=512, description="跳转链接：站内页面路径或外部 URL")
    sort: int = Field(0, description="排序权重，越小越靠前")
    start_time: datetime | None = Field(None, description="生效开始时间，ISO 8601")
    end_time: datetime | None = Field(None, description="生效结束时间，ISO 8601")
    is_active: bool = Field(True, description="是否启用")


class BannerUpdate(BaseModel):
    image_url: str | None = Field(None, min_length=1, max_length=512, description="Banner 图片 URL")
    jump_link: str | None = Field(None, max_length=512, description="跳转链接：站内页面路径或外部 URL")
    sort: int | None = Field(None, description="排序权重，越小越靠前")
    start_time: datetime | None = Field(None, description="生效开始时间，ISO 8601")
    end_time: datetime | None = Field(None, description="生效结束时间，ISO 8601")
    is_active: bool | None = Field(None, description="是否启用")


class BannerListItem(BaseModel):
    id: int = Field(..., description="Banner ID")
    image_url: str = Field(..., description="Banner 图片 URL")
    jump_link: str | None = Field(None, description="跳转链接：站内页面路径或外部 URL")
    sort: int = Field(..., description="排序权重")
    start_time: datetime | None = Field(None, description="生效开始时间")
    end_time: datetime | None = Field(None, description="生效结束时间")
    is_active: bool = Field(..., description="是否启用")
    created_at: datetime = Field(..., description="创建时间")

    model_config = {"from_attributes": True}
