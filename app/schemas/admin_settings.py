from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


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
    role: Literal[
        "quiz_admin", "cert_admin", "course_admin", "teacher", "operations_admin"
    ] = "quiz_admin"
    # 覆盖式按人授权：提供则存库为该管理员的最终权限集（可为空表），
    # 缺省则存 NULL、跟随角色预设。合法性（白名单）由服务层校验。
    permissions: list[str] | None = None

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
    """编辑普通管理员：display_name 与 permissions 至少提供其一。

    permissions 语义（以 model_fields_set 区分「未提供」与显式值）：
    缺省 = 不修改；显式 null = 清除按人覆盖、回退角色预设；列表 = 覆盖。
    """

    display_name: str | None = Field(None, min_length=1, max_length=64)
    permissions: list[str] | None = None

    model_config = {"extra": "forbid"}

    @field_validator("display_name", mode="before")
    @classmethod
    def normalize_display_name(cls, value: object) -> object:
        return _normalize_display_name(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def require_something_to_update(self) -> "AdminSettingsUserUpdate":
        if self.display_name is None and "permissions" not in self.model_fields_set:
            raise ValueError("display_name 与 permissions 至少提供一项")
        return self


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
    role: Literal[
        "super_admin", "quiz_admin", "cert_admin", "course_admin", "teacher", "operations_admin"
    ]
    permissions: list[str] = Field(default_factory=list)
    permissions_source: Literal["wildcard", "preset", "override"] = "preset"
    is_active: bool
    must_change_password: bool
    locked_until: datetime | None = None
    last_login_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @model_validator(mode="before")
    @classmethod
    def _resolve_permissions(cls, value: object) -> object:
        # ORM 对象传入时先展开为 dict 并计算大类授权（编辑弹窗勾选口径）；
        # 显式 dict（测试构造）原样放行。
        from app.policy.permissions import resolve_granted_permissions

        if not hasattr(value, "role"):
            return value
        data = {
            field: getattr(value, field)
            for field in (
                "id",
                "username",
                "display_name",
                "role",
                "is_active",
                "must_change_password",
                "locked_until",
                "last_login_at",
                "created_at",
                "updated_at",
            )
        }
        data["permissions"] = resolve_granted_permissions(value)
        data["permissions_source"] = (
            "wildcard"
            if value.role == "super_admin"
            else "override"
            if getattr(value, "permissions", None) is not None
            else "preset"
        )
        return data


class AdminPermissionCatalogItem(BaseModel):
    permission: str
    label: str


class AdminPermissionCatalogGroup(BaseModel):
    key: str
    label: str
    permissions: list[AdminPermissionCatalogItem]


class AdminPermissionCatalogResponse(BaseModel):
    groups: list[AdminPermissionCatalogGroup]
    role_presets: dict[str, list[str]]


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
