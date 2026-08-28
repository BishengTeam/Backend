from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

CertType = Literal["h3c", "renshe"]
PriceUserType = Literal["student", "normal"]


class CertProductPrice(BaseModel):
    user_type: PriceUserType = Field(..., description="价格档位：student / normal")
    price_cents: int = Field(..., ge=0, description="价格，单位分")


class CertProductResponse(BaseModel):
    id: int
    type: str
    code: str
    name: str
    chinese_name: str
    description: str | None
    is_active: bool
    sort_order: int
    prices: list[CertProductPrice] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CertProductCatalogResponse(BaseModel):
    id: int
    type: str
    code: str
    name: str
    duration_minutes: int | None
    question_count: int | None
    total_score: int | None
    pass_score: int | None
    cert_validity_years: int | None
    retake_count: int | None
    prerequisite: str | None
    remark: str | None
    source: str | None
    instantiated: bool = False

    model_config = {"from_attributes": True}


class CertProductCreate(BaseModel):
    type: CertType = Field(..., description="认证类型：h3c / renshe")
    catalog_id: int | None = Field(None, description="目录项 ID，从目录创建时传入")
    code: str = Field(..., min_length=1, max_length=32, description="产品编码，如 H3CNE")
    name: str = Field(..., min_length=1, max_length=64, description="英文名")
    chinese_name: str = Field(..., min_length=1, max_length=128, description="中文名")
    description: str | None = Field(None, max_length=512)
    is_active: bool = Field(True)
    sort_order: int = Field(0, ge=0)
    prices: list[CertProductPrice] = Field(default_factory=list)


class CertProductUpdate(BaseModel):
    code: str | None = Field(None, min_length=1, max_length=32)
    name: str | None = Field(None, min_length=1, max_length=64)
    chinese_name: str | None = Field(None, min_length=1, max_length=128)
    description: str | None = Field(None, max_length=512)
    is_active: bool | None = None
    sort_order: int | None = Field(None, ge=0)
    prices: list[CertProductPrice] | None = None


class CertProductStats(BaseModel):
    type: str
    type_label: str
    product_count: int
    active_product_count: int
    active_batch_count: int
    total_enrolled: int
