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


ROLE_PERMISSIONS: dict[str, list[str]] = {
    "super_admin": ["*"],
    "content_editor": [
        "dashboard:view",
        "quiz:list", "quiz:write", "quiz:import",
        "content:list", "content:write", "content:banner",
        "course:list", "course:write",
    ],
    "customer_service": [
        "dashboard:view",
        "user:list",
        "user:write",
        "user:delete",
        "order:list",
    ],
    "finance": [
        "dashboard:view",
        "order:list", "order:write",
    ],
    "auditor": [
        "dashboard:view",
        "user:list", "order:list",
        "quiz:list", "content:list", "course:list",
    ],
}

ALL_PERMISSIONS = [
    "dashboard:view",
    "user:list",
    "user:write",
    "user:delete",
    "order:list",
    "order:write",
    "quiz:list",
    "quiz:write",
    "quiz:import",
    "content:list",
    "content:write",
    "content:banner",
    "course:list",
    "course:write",
]


class AdminLoginResponse(BaseModel):
    access_token: str
    expires_in: int
    admin: AdminInfo
    permissions: list[str] = ALL_PERMISSIONS


class AdminMeResponse(BaseModel):
    admin: AdminInfo
    permissions: list[str]


# ── User management ──


class AdminUserFilter(BaseModel):
    openid: str | None = None
    phone: str | None = None
    created_at_start: datetime | None = None
    created_at_end: datetime | None = None


class AdminUserListItem(BaseModel):
    id: int
    openid: str
    phone: str | None = None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class AdminUserUpdate(BaseModel):
    is_active: bool


class AdminUserStatusToggle(BaseModel):
    is_active: bool


class AdminBatchDeleteRequest(BaseModel):
    ids: list[int] = Field(..., min_length=1)
