from datetime import datetime

from pydantic import BaseModel, Field


class ShareCreateRequest(BaseModel):
    target_type: str = Field(..., description="分享目标类型: course / activity / cert 等")
    target_id: int = Field(..., description="目标资源 ID")


class ShareResponse(BaseModel):
    id: int
    code: str
    target_type: str
    target_id: int
    visit_count: int
    created_at: datetime
    model_config = {"from_attributes": True}


class ShareCreateResponse(BaseModel):
    code: str
    share_url: str
