from datetime import datetime

from pydantic import BaseModel, Field


class PopupCreate(BaseModel):
    image_url: str = Field(..., min_length=1, max_length=512, description="弹窗海报图片 URL")
    jump_link: str | None = Field(None, max_length=512, description="跳转链接：站内页面路径（如 /pages/course/detail?id=1）")
    sort: int = Field(0, description="后台列表排序；多条上架时小程序端随机展示")
    start_time: datetime | None = Field(None, description="生效开始时间，ISO 8601")
    end_time: datetime | None = Field(None, description="生效结束时间，ISO 8601")
    is_active: bool = Field(True, description="是否启用")


class PopupUpdate(BaseModel):
    image_url: str | None = Field(None, min_length=1, max_length=512, description="弹窗海报图片 URL")
    jump_link: str | None = Field(None, max_length=512, description="跳转链接：站内页面路径")
    sort: int | None = Field(None, description="后台列表排序；多条上架时小程序端随机展示")
    start_time: datetime | None = Field(None, description="生效开始时间")
    end_time: datetime | None = Field(None, description="生效结束时间")
    is_active: bool | None = Field(None, description="是否启用")


class PopupListItem(BaseModel):
    id: int = Field(..., description="Popup ID")
    image_url: str = Field(..., description="弹窗海报图片 URL")
    jump_link: str | None = Field(None, description="跳转链接：站内页面路径")
    sort: int = Field(..., description="排序权重")
    start_time: datetime | None = Field(None, description="生效开始时间")
    end_time: datetime | None = Field(None, description="生效结束时间")
    is_active: bool = Field(..., description="是否启用")
    created_at: datetime = Field(..., description="创建时间")

    model_config = {"from_attributes": True}
