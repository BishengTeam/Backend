import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class OrderCreate(BaseModel):
    cert_type: str = Field(..., min_length=1, max_length=64, description="认证类型代码，如 H3C-NE")
    candidate_name: str = Field(..., min_length=1, max_length=64, description="考生姓名")
    candidate_phone: str = Field(..., min_length=1, max_length=20, description="考生手机号")
    candidate_idcard: str | None = Field(None, max_length=20, description="考生身份证号")

    @field_validator("candidate_phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        if not re.fullmatch(r"1[3-9]\d{9}", v):
            raise ValueError("手机号格式不正确")
        return v


class OrderResponse(BaseModel):
    id: int
    cert_type: str = Field(..., description="认证类型代码")
    candidate_name: str = Field(..., description="考生姓名")
    candidate_phone: str = Field(..., description="考生手机号")
    candidate_idcard: str | None = Field(None, description="考生身份证号")
    price: int = Field(..., description="订单金额，单位为分")
    status: str = Field(..., description="订单状态：pending / paid / completed / refunded")
    out_trade_no: str | None = Field(None, description="商户订单号")
    created_at: datetime

    model_config = {"from_attributes": True}


class OrderDetailResponse(BaseModel):
    id: int
    cert_type: str = Field(..., description="认证类型代码")
    candidate_name: str = Field(..., description="考生姓名")
    candidate_phone: str = Field(..., description="考生手机号")
    candidate_idcard: str | None = Field(None, description="考生身份证号")
    price: int = Field(..., description="订单金额，单位为分")
    status: str = Field(..., description="订单状态：pending / paid / completed / refunded")
    out_trade_no: str | None = Field(None, description="商户订单号")
    transaction_id: str | None = Field(None, description="微信支付交易号")
    paid_at: datetime | None = Field(None, description="支付时间，ISO 8601")
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class OrderFilter(BaseModel):
    status: str | None = Field(None, description="按状态筛选：pending / paid / completed / refunded")
