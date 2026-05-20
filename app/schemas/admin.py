from datetime import datetime

from pydantic import BaseModel, Field


class AdminLoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class AdminInfo(BaseModel):
    id: int
    username: str
    role: str

    model_config = {"from_attributes": True}


class AdminLoginResponse(BaseModel):
    access_token: str
    expires_in: int
    admin: AdminInfo


# ── User management ──


class AdminUserFilter(BaseModel):
    openid: str | None = None
    phone: str | None = None


class AdminUserListItem(BaseModel):
    id: int
    openid: str
    phone: str | None = None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class AdminUserUpdate(BaseModel):
    is_active: bool
