from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


# ── 登录 ──

class LoginRequest(BaseModel):
    code: str = Field(..., min_length=1, description="微信登录 code")


class UserProfile(BaseModel):
    id: int
    openid: str
    nickname: str | None = None
    phone: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int
    user: UserProfile


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int


class LogoutRequest(BaseModel):
    refresh_token: str


class PhoneDecryptRequest(BaseModel):
    encrypted_data: str
    iv: str


# ── Level 1: 基础资料（无需审核） ──

class UserProfileUpdate(BaseModel):
    """修改基础资料，所有字段可选，仅传入要改的字段"""
    nickname: str | None = Field(None, max_length=64, description="昵称/网名")
    email: str | None = Field(None, max_length=128, description="邮箱")
    phone: str | None = Field(None, min_length=11, max_length=11, description="手机号")
    province: str | None = Field(None, max_length=32, description="省份")
    city: str | None = Field(None, max_length=32, description="城市")
    address: str | None = Field(None, max_length=256, description="详细地址")


# ── Level 2: 实名信息（需审核） ──

class RealnameSubmit(BaseModel):
    """提交/修改实名认证信息"""
    user_type: Literal["student"] = Field("student", description="首版固定为 student")
    last_name_zh: str | None = Field(None, max_length=32, description="姓")
    first_name_zh: str | None = Field(None, max_length=32, description="名")
    last_name_en: str | None = Field(None, max_length=64, description="拼音姓")
    first_name_en: str | None = Field(None, max_length=64, description="拼音名")
    real_name: str = Field(..., min_length=1, max_length=64, description="真实姓名")
    id_card_number: str = Field(..., min_length=18, max_length=18, description="18 位身份证号")
    id_card_front_oss: str = Field(..., min_length=1, max_length=512, description="身份证人像面 OSS key")
    id_card_back_oss: str = Field(..., min_length=1, max_length=512, description="身份证国徽面 OSS key")
    avatar_oss: str = Field(..., min_length=1, max_length=512, description="二寸免冠照片 OSS key")
    political_status: str = Field(..., min_length=1, max_length=16, description="政治面貌")
    ethnicity: str = Field(..., min_length=1, max_length=16, description="民族")


class RealnameResponse(BaseModel):
    user_type: Literal["student"] | None = None
    last_name_zh: str | None = None
    first_name_zh: str | None = None
    last_name_en: str | None = None
    first_name_en: str | None = None
    real_name: str | None = None
    id_card_number: str | None = None  # 脱敏
    id_card_front_oss: str | None = None
    id_card_back_oss: str | None = None
    avatar_oss: str | None = None
    birth_date: str | None = None
    gender: str | None = None
    age: int | None = None
    census_register: str | None = None
    zip_code: str | None = None
    political_status: str | None = None
    ethnicity: str | None = None
    status: Literal["pending", "verified", "rejected"] | None = None
    verified_at: str | None = None
    reject_reason: str | None = None  # 最新驳回理由

    model_config = {"from_attributes": True}


class RealnameAdminResponse(RealnameResponse):
    """管理端实名响应 — 身份证不脱敏"""
    id_card_number: str | None = None


# ── Level 2: 学生信息（需审核） ──

class StudentSubmit(BaseModel):
    """提交/修改学生信息"""
    education: Literal[
        "secondary_vocational", "associate", "bachelor", "master", "doctorate"
    ] = Field(..., description="学历枚举")
    school: str = Field(..., min_length=1, max_length=128, description="学校")
    major: str = Field(..., min_length=1, max_length=128, description="专业")
    enrollment_date: date = Field(..., description="入学日期，仅供人工审核")
    student_card_oss: str = Field(..., min_length=1, max_length=512, description="学生证 OSS key")
    enrollment_pdf_oss: str = Field(..., min_length=1, max_length=512, description="学信网电子注册表 OSS key")
    degree_cert_oss: str = Field(..., min_length=1, max_length=512, description="学历证明 OSS key")


class StudentResponse(BaseModel):
    education: str | None = None
    school: str | None = None
    major: str | None = None
    enrollment_date: date | None = None
    student_card_oss: str | None = None
    enrollment_pdf_oss: str | None = None
    degree_cert_oss: str | None = None
    status: Literal["pending", "verified", "rejected"] | None = None
    verified_at: str | None = None
    reject_reason: str | None = None  # 最新驳回理由

    model_config = {"from_attributes": True}


# ── Level 2: 企业信息（需审核） ──

class EnterpriseSubmit(BaseModel):
    """提交/修改企业信息"""
    organization: str = Field(..., min_length=1, max_length=256, description="单位名称")


class EnterpriseResponse(BaseModel):
    organization: str | None = None
    status: Literal["pending", "verified", "rejected"] | None = None
    verified_at: str | None = None
    reject_reason: str | None = None  # 最新驳回理由

    model_config = {"from_attributes": True}


# ── 聚合响应 ──

class UserProfileDetail(BaseModel):
    """GET /api/user/profile 或 GET /admin/users/{id}/profile 聚合响应"""
    id: int
    openid: str
    # Level 1
    nickname: str | None = None
    email: str | None = None
    phone: str | None = None  # 小程序端脱敏，管理端明文
    province: str | None = None
    city: str | None = None
    address: str | None = None
    # Level 2 — 实名
    user_type: str | None = None
    last_name_zh: str | None = None
    first_name_zh: str | None = None
    last_name_en: str | None = None
    first_name_en: str | None = None
    real_name: str | None = None
    id_card: str | None = None        # 脱敏
    id_card_raw: str | None = None    # 管理端明文
    id_card_front_oss: str | None = None
    id_card_back_oss: str | None = None
    avatar_oss: str | None = None
    birth_date: str | None = None
    gender: str | None = None
    age: int | None = None
    census_register: str | None = None
    zip_code: str | None = None
    political_status: str | None = None
    ethnicity: str | None = None
    identity_status: str | None = None  # pending / verified / rejected
    # Level 2 — 学生
    education: str | None = None
    school: str | None = None
    major: str | None = None
    enrollment_date: date | None = None
    student_card_oss: str | None = None
    enrollment_pdf_oss: str | None = None
    degree_cert_oss: str | None = None
    student_status: str | None = None
    # 审核反馈
    identity_reject_reason: str | None = None
    student_reject_reason: str | None = None
    # 修改次数
    edit_count: int = 0              # 已用修改次数
    edit_count_limit: int = 10       # 修改次数上限
    edit_count_reset_hours: int | None = None  # 多少小时后重置（已用次数时有效）
    # 其他
    created_at: str = ""

    model_config = {"from_attributes": True}


class UserUnbindRequest(BaseModel):
    type: Literal["phone", "wechat"] = Field(..., description="解绑类型：phone 手机号 / wechat 微信")
