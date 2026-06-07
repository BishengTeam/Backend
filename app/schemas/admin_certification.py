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
    name: str = Field(..., min_length=1, max_length=64)
    chinese_name: str = Field(..., min_length=1, max_length=128)
    code: str = Field(..., min_length=1, max_length=32)
    vendor: str = Field(..., min_length=1, max_length=64)
    requires_xuexin: bool = False
    pay_first: bool = False
    is_active: bool = True


class AdminCertificationUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=64)
    chinese_name: str | None = Field(None, min_length=1, max_length=128)
    code: str | None = Field(None, min_length=1, max_length=32)
    vendor: str | None = Field(None, min_length=1, max_length=64)
    requires_xuexin: bool | None = None
    pay_first: bool | None = None
    is_active: bool | None = None
