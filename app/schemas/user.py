from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    code: str = Field(..., min_length=1, description="微信登录 code")


class UserProfile(BaseModel):
    id: int
    openid: str
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


class UserIdentityCreate(BaseModel):
    user_type: Literal["student", "enterprise"] = Field(..., description="用户类型：学生 / 企业")
    real_name: str = Field(..., min_length=1, max_length=64, description="真实姓名")
    id_card_number: str = Field(..., min_length=18, max_length=18, description="18 位身份证号")
    id_card_front_oss: str | None = Field(None, max_length=512, description="身份证人像面 OSS object key")
    id_card_back_oss: str | None = Field(None, max_length=512, description="身份证国徽面 OSS object key")
    student_card_oss: str | None = Field(None, max_length=512, description="学生证 OSS object key，学生用户必传")


class UserIdentityResponse(BaseModel):
    user_type: Literal["student", "enterprise"] = Field(..., description="用户类型")
    real_name: str = Field(..., description="真实姓名")
    id_card_number: str = Field(..., description="身份证号，脱敏返回（前 4 + 后 4，中间掩码）")
    id_card_front_oss: str | None = Field(None, description="身份证人像面 OSS key")
    id_card_back_oss: str | None = Field(None, description="身份证国徽面 OSS key")
    student_card_oss: str | None = Field(None, description="学生证 OSS key")
    status: Literal["pending", "verified", "rejected"] = Field(..., description="审核状态")
    verified_at: str | None = Field(None, description="审核通过时间，ISO 8601")
    created_at: str = Field(..., description="创建时间，ISO 8601")

    model_config = {"from_attributes": True}


class UserProfileDetail(BaseModel):
    id: int
    openid: str
    phone: str | None = None
    email: str | None = None
    real_name: str | None = None
    id_card: str | None = None
    user_type: str | None = None
    gender: str | None = None
    education: str | None = None
    school: str | None = None
    major: str | None = None
    organization: str | None = None
    identity_status: str | None = None
    created_at: datetime | str

    model_config = {"from_attributes": True}


class UserProfileUpdate(BaseModel):
    phone: str | None = Field(None, min_length=11, max_length=11, description="手机号")
    email: str | None = Field(None, max_length=128, description="邮箱")
    gender: str | None = Field(None, max_length=8, description="性别")
    education: str | None = Field(None, max_length=32, description="学历")
    school: str | None = Field(None, max_length=128, description="学校")
    major: str | None = Field(None, max_length=128, description="专业")
    organization: str | None = Field(None, max_length=256, description="单位")


class UserUnbindRequest(BaseModel):
    type: Literal["phone", "wechat"] = Field(..., description="解绑类型：phone 手机号 / wechat 微信")
