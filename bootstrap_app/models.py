from __future__ import annotations

import ipaddress
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


HOST_RE = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,62}$")
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
ADMIN_USERNAME_RE = re.compile(r"^[a-z][a-z0-9._-]{3,31}$")
FROZEN_WEAK_ADMIN_PASSWORDS = frozenset(
    {
        "password1234",
        "password12345",
        "qwerty123456",
        "qwertyuiop12",
        "abc123456789",
        "administrator1",
        "welcome12345",
        "letmein123456",
        "1234567890ab",
    }
)


def _clean_single_line(value: str, *, field_name: str, max_length: int = 2048) -> str:
    cleaned = value.strip()
    if not cleaned or "\x00" in cleaned or "\r" in cleaned or "\n" in cleaned:
        raise ValueError(f"{field_name} must be a non-empty single-line value")
    if len(cleaned) > max_length:
        raise ValueError(f"{field_name} is too long")
    return cleaned


def _https_origin(value: str, *, field_name: str) -> str:
    cleaned = _clean_single_line(value, field_name=field_name, max_length=512).rstrip("/")
    parsed = urlsplit(cleaned)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError(f"{field_name} must be an HTTPS origin without path or credentials")
    return cleaned


def _https_endpoint(value: str, *, field_name: str) -> str:
    cleaned = _clean_single_line(value, field_name=field_name, max_length=512).rstrip("/")
    parsed = urlsplit(cleaned)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"{field_name} must be an HTTPS endpoint")
    return cleaned


class BootstrapConfigureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deployment_mode: Literal["internal", "external"]
    api_origin: str
    admin_origin: str

    postgres_host: str = "db"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_user: str = "wemini"
    postgres_database: str = "wemini_app"
    postgres_password: SecretStr | None = None
    redis_url: SecretStr | None = None

    wechat_appid: str
    wechat_secret: SecretStr
    wechat_pay_mchid: str
    wechat_pay_appid: str
    wechat_pay_cert_serial_no: str
    wechat_pay_private_key_pem: SecretStr
    wechat_pay_api_v3_key: SecretStr
    wechat_pay_public_key_pem: SecretStr
    wechat_pay_public_key_id: str

    oss_endpoint: str | None = None
    oss_bucket: str | None = None
    oss_access_key_id: SecretStr | None = None
    oss_access_key_secret: SecretStr | None = None
    recovery_public_key_pem: SecretStr

    @field_validator(
        "oss_endpoint",
        "oss_bucket",
        "oss_access_key_id",
        "oss_access_key_secret",
        mode="before",
    )
    @classmethod
    def empty_optional_oss_value(cls, value):
        """Treat an empty browser field as an omitted optional credential."""

        if value is None:
            return None
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned or None
        return value

    @field_validator("api_origin")
    @classmethod
    def validate_api_origin(cls, value: str) -> str:
        return _https_origin(value, field_name="api_origin")

    @field_validator("admin_origin")
    @classmethod
    def validate_admin_origin(cls, value: str) -> str:
        return _https_origin(value, field_name="admin_origin")

    @field_validator(
        "oss_endpoint",
    )
    @classmethod
    def validate_endpoint(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        return _https_endpoint(value, field_name=info.field_name)

    @field_validator(
        "oss_bucket",
    )
    @classmethod
    def validate_bucket(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        cleaned = _clean_single_line(value, field_name=info.field_name, max_length=63)
        if not BUCKET_RE.fullmatch(cleaned):
            raise ValueError(f"{info.field_name} has an invalid OSS bucket name")
        return cleaned

    @field_validator("postgres_host")
    @classmethod
    def validate_postgres_host(cls, value: str) -> str:
        cleaned = _clean_single_line(value, field_name="postgres_host", max_length=253)
        candidate = cleaned.strip("[]")
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            if not HOST_RE.fullmatch(cleaned):
                raise ValueError("postgres_host is invalid")
        return cleaned

    @field_validator("postgres_user", "postgres_database")
    @classmethod
    def validate_database_name(cls, value: str, info) -> str:
        cleaned = _clean_single_line(value, field_name=info.field_name, max_length=63)
        if not NAME_RE.fullmatch(cleaned):
            raise ValueError(f"{info.field_name} is invalid")
        return cleaned

    @field_validator(
        "wechat_appid",
        "wechat_pay_appid",
        "wechat_pay_mchid",
        "wechat_pay_cert_serial_no",
        "wechat_pay_public_key_id",
    )
    @classmethod
    def validate_payment_identifiers(cls, value: str, info) -> str:
        return _clean_single_line(value, field_name=info.field_name, max_length=128)

    @model_validator(mode="after")
    def validate_modes_and_origins(self) -> "BootstrapConfigureRequest":
        if self.api_origin == self.admin_origin:
            raise ValueError("api_origin and admin_origin must be different")
        fields = (
            "oss_endpoint",
            "oss_bucket",
            "oss_access_key_id",
            "oss_access_key_secret",
        )
        present = [self._optional_value_present(getattr(self, name)) for name in fields]
        if any(present) and not all(present):
            missing = [name for name, exists in zip(fields, present) if not exists]
            raise ValueError(
                "oss must be fully configured or left empty; missing: "
                + ", ".join(missing)
            )
        if self.deployment_mode == "external":
            if self.postgres_host == "db":
                raise ValueError("external deployment requires an explicit postgres_host")
            if (
                self.postgres_password is None
                or not self.postgres_password.get_secret_value()
            ):
                raise ValueError("external deployment requires postgres_password")
            if self.redis_url is None or not self.redis_url.get_secret_value():
                raise ValueError("external deployment requires redis_url")
            redis_scheme = urlsplit(self.redis_url.get_secret_value()).scheme
            if redis_scheme not in {"redis", "rediss"}:
                raise ValueError("redis_url must use redis:// or rediss://")
        return self

    @staticmethod
    def _optional_value_present(value: object) -> bool:
        if isinstance(value, SecretStr):
            return bool(value.get_secret_value())
        return bool(value)

    def has_renshe_oss(self) -> bool:
        return self.has_oss()

    def has_quiz_oss(self) -> bool:
        return self.has_oss()

    def has_recovery_oss(self) -> bool:
        return self.has_oss()

    def has_oss(self) -> bool:
        return self.oss_endpoint is not None


class BootstrapAdminRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=4, max_length=32)
    password: SecretStr

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        cleaned = _clean_single_line(value, field_name="username", max_length=32).lower()
        if not ADMIN_USERNAME_RE.fullmatch(cleaned):
            raise ValueError("username contains unsupported characters")
        return cleaned

    @model_validator(mode="after")
    def validate_password(self) -> "BootstrapAdminRequest":
        password = self.password.get_secret_value()
        if len(password) < 12 or len(password) > 128:
            raise ValueError("password must contain 12 to 128 characters")
        if "\x00" in password or "\r" in password or "\n" in password:
            raise ValueError("password contains unsupported control characters")
        if not re.search(r"[A-Za-z]", password) or not re.search(r"[0-9]", password):
            raise ValueError("password must contain letters and numbers")
        if self.username in password.lower():
            raise ValueError("password must not contain the username")
        if password.casefold() in FROZEN_WEAK_ADMIN_PASSWORDS:
            raise ValueError("password is too common")
        return self


class BootstrapFailureResponse(BaseModel):
    code: str
    stage: str
    occurred_at: str


class BootstrapStatusResponse(BaseModel):
    version: int
    installation_id: str
    phase: str
    created_at: str
    updated_at: str
    retry_count: int
    config_fingerprint: str | None = None
    backend_commit: str | None = None
    admin_commit: str | None = None
    release_manifest_sha256: str | None = None
    recovery_object_key: str | None = None
    recovery_sha256: str | None = None
    last_failure: BootstrapFailureResponse | None = None
