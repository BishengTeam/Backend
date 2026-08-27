from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


ADMIN_USERNAME_PATTERN = r"^[a-z][a-z0-9._-]{3,31}$"


def _normalize_username(value: str) -> str:
    return value.strip().lower()


def _normalize_display_name(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("管理员显示名不能为空")
    return normalized


class AdminSettingsUserCreate(BaseModel):
    username: str = Field(..., pattern=ADMIN_USERNAME_PATTERN)
    display_name: str = Field(..., min_length=1, max_length=64)
    # The creation API deliberately omits ``super_admin`` from this literal.
    # Super admin remains non-creatable; fixed staff roles are code-owned.
    role: Literal["quiz_admin", "cert_admin", "course_admin"] = "quiz_admin"

    model_config = {"extra": "forbid"}

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value: object) -> object:
        return _normalize_username(value) if isinstance(value, str) else value

    @field_validator("display_name", mode="before")
    @classmethod
    def normalize_display_name(cls, value: object) -> object:
        return _normalize_display_name(value) if isinstance(value, str) else value


class AdminSettingsUserUpdate(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=64)

    model_config = {"extra": "forbid"}

    @field_validator("display_name", mode="before")
    @classmethod
    def normalize_display_name(cls, value: object) -> object:
        return _normalize_display_name(value) if isinstance(value, str) else value


class AdminSettingsLegacyUserUpdate(BaseModel):
    """旧 PUT 路由的兼容请求；新客户端必须使用 PATCH/动作路由。"""

    display_name: str | None = Field(None, min_length=1, max_length=64)
    is_active: bool | None = None

    model_config = {"extra": "forbid"}

    @field_validator("display_name", mode="before")
    @classmethod
    def normalize_display_name(cls, value: object) -> object:
        return _normalize_display_name(value) if isinstance(value, str) else value


class AdminSettingsPasswordReset(BaseModel):
    """保留名称以兼容旧导入；新重置接口没有请求字段。"""

    model_config = {"extra": "forbid"}


class AdminSettingsUserListItem(BaseModel):
    id: int
    username: str
    display_name: str
    role: Literal["super_admin", "quiz_admin", "cert_admin", "course_admin"]
    is_active: bool
    must_change_password: bool
    locked_until: datetime | None = None
    last_login_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AdminSettingsTemporaryPasswordResponse(BaseModel):
    admin: AdminSettingsUserListItem
    temporary_password: str = Field(
        ..., min_length=12, max_length=128, repr=False
    )


class AdminSecurityAuditListItem(BaseModel):
    id: int
    action: str
    result: Literal["succeeded", "failed"]
    reason_code: str | None = None
    actor_admin_id: int | None = None
    target_admin_id: int | None = None
    username: str | None = None
    request_id: str | None = None
    source_ip: str | None = None
    user_agent: str | None = None
    summary: dict | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
