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


class PhoneDecryptRequest(BaseModel):
    encrypted_data: str
    iv: str


class UserIdentityCreate(BaseModel):
    real_name: str = Field(..., min_length=1, max_length=64, description="真实姓名")
    id_card_number: str = Field(..., min_length=18, max_length=18, description="18 位身份证号")
    id_card_front_oss: str | None = Field(None, max_length=512, description="身份证人像面 OSS object key")
    id_card_back_oss: str | None = Field(None, max_length=512, description="身份证国徽面 OSS object key")
    student_card_oss: str | None = Field(None, max_length=512, description="学生证 OSS object key")


class UserIdentityResponse(BaseModel):
    real_name: str
    id_card_number: str
    id_card_front_oss: str | None = None
    id_card_back_oss: str | None = None
    student_card_oss: str | None = None
    status: Literal["pending", "verified", "rejected"]
    verified_at: str | None = None
    created_at: str

    model_config = {"from_attributes": True}
