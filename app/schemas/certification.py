from typing import Literal

from datetime import datetime

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


class CertificationDetailResponse(CertificationResponse):
    """认证详情，继承列表字段 + 时间戳"""
    created_at: datetime
    updated_at: datetime


# ── P2 深信服/NISP ──────────────────────────────────────────────

class SangforCouponResponse(BaseModel):
    """深信服考试券"""
    id: int
    name: str
    chinese_name: str
    code: str

    model_config = {"from_attributes": True}


class VerifyCodeResponse(BaseModel):
    """动态验证码"""
    code: str


class PinyinResponse(BaseModel):
    """拼音转换结果"""
    pinyin: str


class NispTemplateResponse(BaseModel):
    """NISP 模板响应"""
    message: str
    template_url: str | None


class CertTagResponse(BaseModel):
    code: str
    label: str
