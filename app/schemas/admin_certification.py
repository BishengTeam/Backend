from pydantic import BaseModel, Field

from app.schemas.certification import CertificationDetailResponse, Vendor


class AdminCertificationListItem(CertificationDetailResponse):
    """管理端认证列表项 — 字段与 CertificationDetailResponse 完全一致，通过继承复用
    vendor 类型继承自 CertificationResponse.Vendor（Literal），修复管理端 str 退化。"""


class AdminCertificationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, description="认证名称（英文）")
    chinese_name: str = Field(..., min_length=1, max_length=128, description="认证中文名称")
    code: str = Field(..., min_length=1, max_length=32, description="认证编码")
    vendor: Vendor = Field(..., description="认证厂商")
    requires_xuexin: bool = Field(False, description="是否需要学信网认证")
    pay_first: bool = Field(False, description="是否先付费")
    is_active: bool = Field(True, description="是否上架")


class AdminCertificationUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=64, description="认证名称（英文）")
    chinese_name: str | None = Field(None, min_length=1, max_length=128, description="认证中文名称")
    code: str | None = Field(None, min_length=1, max_length=32, description="认证编码")
    vendor: Vendor | None = Field(None, description="认证厂商")
    requires_xuexin: bool | None = Field(None, description="是否需要学信网认证")
    pay_first: bool | None = Field(None, description="是否先付费")
    is_active: bool | None = Field(None, description="是否上架")