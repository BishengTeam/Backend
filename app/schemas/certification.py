from typing import Literal

from pydantic import BaseModel, Field

Vendor = Literal["H3C", "深信服", "NISP", "人社"]


class CertificationResponse(BaseModel):
    id: int
    name: str
    chinese_name: str
    code: str
    vendor: Vendor
    requires_xuexin: bool
    pay_first: bool = Field(..., description="是否先支付后填写报名表")

    model_config = {"from_attributes": True}


class CertificationFilter(BaseModel):
    vendor: Vendor | None = Field(None, description="按厂商筛选：H3C / 深信服 / NISP / 人社")
