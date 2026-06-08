from datetime import datetime

from pydantic import BaseModel, Field


VALID_TARGET_TYPES = ("cert", "course", "activity", "zone", "url")


class BannerCreate(BaseModel):
    image_url: str = Field(..., min_length=1, max_length=512)
    jump_link: str | None = Field(None, max_length=512)
    target_type: str | None = Field(None, max_length=32, description="cert/course/activity/zone/url")
    target_id: int | None = Field(None, ge=1, description="资源 ID，target_type=url 时为空")
    sort: int = 0
    start_time: datetime | None = None
    end_time: datetime | None = None
    is_active: bool = True


class BannerUpdate(BaseModel):
    image_url: str | None = Field(None, min_length=1, max_length=512)
    jump_link: str | None = Field(None, max_length=512)
    target_type: str | None = Field(None, max_length=32)
    target_id: int | None = Field(None, ge=1)
    sort: int | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    is_active: bool | None = None


class BannerListItem(BaseModel):
    id: int
    image_url: str
    jump_link: str | None = None
    target_type: str | None = None
    target_id: int | None = None
    sort: int
    start_time: datetime | None = None
    end_time: datetime | None = None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
