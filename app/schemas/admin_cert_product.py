from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

CertType = Literal["h3c", "renshe"]


class CertProductResponse(BaseModel):
    id: int
    type: str
    code: str
    name: str
    chinese_name: str
    description: str | None
    is_active: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CertProductCreate(BaseModel):
    type: CertType = Field(..., description="认证类型：h3c / renshe")
    code: str = Field(..., min_length=1, max_length=32, description="产品编码，如 H3CNE")
    name: str = Field(..., min_length=1, max_length=64, description="英文名")
    chinese_name: str = Field(..., min_length=1, max_length=128, description="中文名")
    description: str | None = Field(None, max_length=512)
    is_active: bool = Field(True)
    sort_order: int = Field(0, ge=0)


class CertProductUpdate(BaseModel):
    code: str | None = Field(None, min_length=1, max_length=32)
    name: str | None = Field(None, min_length=1, max_length=64)
    chinese_name: str | None = Field(None, min_length=1, max_length=128)
    description: str | None = Field(None, max_length=512)
    is_active: bool | None = None
    sort_order: int | None = Field(None, ge=0)


class CertProductStats(BaseModel):
    type: str
    type_label: str
    product_count: int
    active_product_count: int
    active_batch_count: int
    total_enrolled: int
