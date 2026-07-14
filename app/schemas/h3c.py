"""H3C 认证报名 Schema"""
from datetime import datetime

from pydantic import BaseModel, Field


class H3cProfileDefaults(BaseModel):
    """GET /api/orders/h3c/profile — 从个人资料预填的默认值"""
    candidate_name: str | None = None
    gender: str | None = None
    candidate_idcard: str | None = None
    school: str | None = None
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    education: str | None = None
    first_name_en: str | None = None
    last_name_en: str | None = None
    birth_date: str | None = None
    degree_cert_oss: str | None = None


class H3cOrderCreate(BaseModel):
    """POST /api/orders/h3c — H3C 认证报名"""
    plan_id: int = Field(..., gt=0, description="报名批次 ID")

    # 预填字段（前端可覆盖）
    candidate_name: str = Field(..., min_length=1, max_length=64, description="考生姓名")
    gender: str = Field(..., min_length=1, max_length=4, description="性别")
    candidate_idcard: str = Field(..., min_length=18, max_length=18, description="身份证号")
    school: str = Field(..., min_length=1, max_length=128, description="学校名称")
    address: str = Field(..., min_length=1, max_length=256, description="地址")
    phone: str = Field(..., description="手机号")
    email: str = Field(..., description="邮箱")
    education: str = Field(..., min_length=1, max_length=32, description="学历")
    first_name_en: str = Field(..., min_length=1, max_length=64, description="拼音名")
    last_name_en: str = Field(..., min_length=1, max_length=64, description="拼音姓")
    birth_date: str | None = Field(None, max_length=10, description="出生年月")

    # H3C 专用必填
    coupon_code: str = Field(..., min_length=1, max_length=64, description="考券号")
    verify_code: str = Field(..., min_length=1, max_length=16, description="在线验证码")
    identity_tag: str = Field(..., min_length=1, max_length=32, description="身份标签")
    exam_datetime: str = Field(..., description="考试时间")

    # H3C 专用可选
    candidate_no: str | None = Field(None, max_length=32, description="考生号")
    training_org: str | None = Field(None, max_length=128, description="培训机构")
    training_address: str | None = Field(None, max_length=256, description="培训地址")
    training_start: str | None = Field(None, description="培训开始时间")
    training_end: str | None = Field(None, description="培训结束时间")

    # 文件
    coupon_proof_oss: str = Field(..., min_length=1, max_length=512, description="优惠券证明图片 OSS key")
    degree_cert_oss: str = Field(..., min_length=1, max_length=512, description="学历证明图片 OSS key")


class H3cOrderResponse(BaseModel):
    """H3C 报名订单响应"""
    id: int
    plan_id: int
    order_kind: str
    product_type: str
    candidate_name: str | None
    candidate_phone: str | None
    candidate_idcard: str | None
    price: int
    status: str
    out_trade_no: str | None
    inventory_id: int | None
    expires_at: datetime | None
    created_at: datetime
    extra_data: dict | None
    attachments: list[str] | None

    model_config = {"from_attributes": True}
