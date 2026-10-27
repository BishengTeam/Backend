from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

CertType = Literal["h3c", "renshe", "nisp", "sangfor"]
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
    # 首页认证专区通栏大图（1404×640）
    home_cover_url: str | None
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
    prices: list[CertProductPrice] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class CertProductCatalogImportResponse(BaseModel):
    imported_count: int = Field(..., ge=0)
    created_count: int = Field(..., ge=0)
    updated_count: int = Field(..., ge=0)
    price_upserted_count: int = Field(..., ge=0)
    source: str = Field(..., max_length=128)


class CertProductCreate(BaseModel):
    type: CertType = Field(..., description="认证类型：h3c / renshe / nisp / sangfor")
    catalog_id: int | None = Field(None, description="目录项 ID，从目录创建时传入")
    code: str = Field(..., min_length=1, max_length=32, description="产品编码，如 H3CNE")
    name: str = Field(..., min_length=1, max_length=64, description="英文名")
    chinese_name: str = Field(..., min_length=1, max_length=128, description="中文名")
    description: str | None = Field(None, max_length=512)
    home_cover_url: str | None = Field(None, max_length=512, description="首页认证专区通栏大图（1404×640）")
    is_active: bool = Field(True)
    sort_order: int = Field(0, ge=0)
    prices: list[CertProductPrice] | None = Field(
        None,
        description="可选历史标准价格；省略或 null 保持现有参考价，空列表显式停用。H3C 报名实收由批次三档价格决定",
    )


class CertProductUpdate(BaseModel):
    code: str | None = Field(None, min_length=1, max_length=32)
    name: str | None = Field(None, min_length=1, max_length=64)
    chinese_name: str | None = Field(None, min_length=1, max_length=128)
    description: str | None = Field(None, max_length=512)
    home_cover_url: str | None = Field(None, max_length=512, description="首页认证专区通栏大图（1404×640）")
    is_active: bool | None = None
    sort_order: int | None = Field(None, ge=0)
    prices: list[CertProductPrice] | None = Field(
        None,
        description="省略或 null 保持现有标准价格，空列表显式停用；H3C 产品参考价不决定批次报名实收",
    )


class CertProductStats(BaseModel):
    type: str
    type_label: str
    product_count: int
    active_product_count: int
    active_batch_count: int
    total_enrolled: int
