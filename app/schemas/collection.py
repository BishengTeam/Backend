from datetime import datetime

from pydantic import BaseModel, Field


class CollectionCreate(BaseModel):
    target_type: str = Field(..., description="收藏类型: course / material")
    target_id: int = Field(..., description="目标资源 ID")


class CollectionResponse(BaseModel):
    id: int
    target_type: str
    target_id: int
    created_at: datetime

    model_config = {"from_attributes": True}
