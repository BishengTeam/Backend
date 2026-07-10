from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.certification import Vendor


class AdminCertificationListItem(BaseModel):
    id: int
    code: str
    vendor: Vendor
    normal_price: int | None = Field(None, description="普通价格，单位分")
    student_price: int | None = Field(None, description="学生价格，单位分")
    is_active: bool
    created_at: datetime
    updated_at: datetime


class AdminCertificationCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=32, description="认证编码")
    vendor: Vendor = Field(..., description="认证厂商")
    normal_price: int = Field(..., ge=0, description="普通价格，单位分")
    student_price: int = Field(..., ge=0, description="学生价格，单位分")
    is_active: bool = Field(True, description="是否上架")


class AdminCertificationUpdate(BaseModel):
    code: str | None = Field(None, min_length=1, max_length=32, description="认证编码")
    vendor: Vendor | None = Field(None, description="认证厂商")
    normal_price: int | None = Field(None, ge=0, description="普通价格，单位分")
    student_price: int | None = Field(None, ge=0, description="学生价格，单位分")
    is_active: bool | None = Field(None, description="是否上架")
