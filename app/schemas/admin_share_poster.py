from datetime import datetime

from pydantic import BaseModel, Field


class SharePosterCreate(BaseModel):
    image_url: str = Field(..., min_length=1, max_length=512, description="分享海报图片 URL（建议 1200×1600）")
    is_active: bool = Field(True, description="是否启用")


class SharePosterUpdate(BaseModel):
    image_url: str | None = Field(None, min_length=1, max_length=512, description="分享海报图片 URL")
    is_active: bool | None = Field(None, description="是否启用")


class SharePosterListItem(BaseModel):
    id: int = Field(..., description="SharePoster ID")
    image_url: str = Field(..., description="分享海报图片 URL")
    is_active: bool = Field(..., description="是否启用")
    created_at: datetime = Field(..., description="创建时间")

    model_config = {"from_attributes": True}
