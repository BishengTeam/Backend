from datetime import datetime

from pydantic import BaseModel, Field


class AdminCertificationListItem(BaseModel):
    id: int
    name: str
    chinese_name: str
    code: str
    vendor: str
    requires_xuexin: bool
    pay_first: bool
    is_active: bool
    created_at: datetime
    model_config = {"from_attributes": True}


class AdminCertificationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, description="认证名称（英文）")
    chinese_name: str = Field(..., min_length=1, max_length=128, description="认证中文名称")
    code: str = Field(..., min_length=1, max_length=32, description="认证编码")
    vendor: str = Field(..., min_length=1, max_length=64, description="认证厂商")
    requires_xuexin: bool = Field(False, description="是否需要学信网认证")
    pay_first: bool = Field(False, description="是否先付费")
    is_active: bool = Field(True, description="是否上架")


class AdminCertificationUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=64, description="认证名称（英文）")
    chinese_name: str | None = Field(None, min_length=1, max_length=128, description="认证中文名称")
    code: str | None = Field(None, min_length=1, max_length=32, description="认证编码")
    vendor: str | None = Field(None, min_length=1, max_length=64, description="认证厂商")
    requires_xuexin: bool | None = Field(None, description="是否需要学信网认证")
    pay_first: bool | None = Field(None, description="是否先付费")
    is_active: bool | None = Field(None, description="是否上架")