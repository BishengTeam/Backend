from datetime import datetime

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    code: str = Field(..., min_length=1, description="微信登录 code")


class UserProfile(BaseModel):
    id: int
    openid: str
    phone: str | None = None
    user_type: str
    age_group: str | None = None
    certification_interest: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int
    user: UserProfile
    poster_url: str | None = None


class UserProfileUpdate(BaseModel):
    phone: str | None = Field(None, max_length=20)
    age_group: str | None = Field(None, max_length=16)
    certification_interest: str | None = Field(None, max_length=64)


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int


class PhoneDecryptRequest(BaseModel):
    encrypted_data: str
    iv: str
