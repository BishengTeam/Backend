from datetime import datetime

from pydantic import BaseModel, Field


class AgreementCreate(BaseModel):
    type: str = Field(..., min_length=1, max_length=32, description="协议类型")
    content: str | None = Field(None, description="协议文本")


class AgreementSign(BaseModel):
    signature_image: str = Field(..., min_length=1, max_length=512, description="签名图片 OSS key 或 base64 字符串")


class AgreementResponse(BaseModel):
    id: int
    user_id: int
    type: str
    content: str | None = None
    signature_image: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
