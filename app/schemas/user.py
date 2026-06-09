from datetime import datetime
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


# ── Level 2: 实名信息（需审核） ──

class RealnameSubmit(BaseModel):
    """提交/修改实名认证信息"""
    user_type: Literal["student", "enterprise"] = Field(..., description="用户类型")
    real_name: str = Field(..., min_length=1, max_length=64, description="真实姓名")
    id_card_number: str = Field(..., min_length=18, max_length=18, description="18 位身份证号")
    id_card_front_oss: str = Field(..., min_length=1, max_length=512, description="身份证人像面 OSS key")
    id_card_back_oss: str = Field(..., min_length=1, max_length=512, description="身份证国徽面 OSS key")


class RealnameResponse(BaseModel):
    user_type: Literal["student", "enterprise"] | None = None
    real_name: str | None = None
    id_card_number: str | None = None  # 脱敏
    id_card_front_oss: str | None = None
    id_card_back_oss: str | None = None
    gender: str | None = None
    age: int | None = None
    census_register: str | None = None
    status: Literal["pending", "verified", "rejected"] | None = None
    verified_at: str | None = None

    model_config = {"from_attributes": True}


class RealnameAdminResponse(RealnameResponse):
    """管理端实名响应 — 身份证不脱敏"""
    id_card_number: str | None = None


# ── Level 2: 学生信息（需审核） ──

class StudentSubmit(BaseModel):
    """提交/修改学生信息"""
    education: str = Field(..., min_length=1, max_length=32, description="学历")
    school: str = Field(..., min_length=1, max_length=128, description="学校")
    major: str = Field(..., min_length=1, max_length=128, description="专业")
    student_card_oss: str = Field(..., min_length=1, max_length=512, description="学生证 OSS key")


class StudentResponse(BaseModel):
    education: str | None = None
    school: str | None = None
    major: str | None = None
    student_card_oss: str | None = None
    status: Literal["pending", "verified", "rejected"] | None = None
    verified_at: str | None = None

    model_config = {"from_attributes": True}


# ── Level 2: 企业信息（需审核） ──

class EnterpriseSubmit(BaseModel):
    """提交/修改企业信息"""
    organization: str = Field(..., min_length=1, max_length=256, description="单位名称")


class EnterpriseResponse(BaseModel):
    organization: str | None = None
    status: Literal["pending", "verified", "rejected"] | None = None
    verified_at: str | None = None

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
    # Level 2 — 实名
    user_type: str | None = None
    real_name: str | None = None
    id_card: str | None = None        # 脱敏
    id_card_raw: str | None = None    # 管理端明文
    id_card_front_oss: str | None = None
    id_card_back_oss: str | None = None
    gender: str | None = None
    age: int | None = None
    census_register: str | None = None
    identity_status: str | None = None  # pending / verified / rejected
    # Level 2 — 学生
    education: str | None = None
    school: str | None = None
    major: str | None = None
    student_card_oss: str | None = None
    student_status: str | None = None
    # Level 2 — 企业
    organization: str | None = None
    enterprise_status: str | None = None
    # 其他
    created_at: str = ""

    model_config = {"from_attributes": True}


class UserUnbindRequest(BaseModel):
    type: Literal["phone", "wechat"] = Field(..., description="解绑类型：phone 手机号 / wechat 微信")
