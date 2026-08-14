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

    renshe_oss_endpoint: str
    renshe_oss_bucket: str
    renshe_oss_access_key_id: SecretStr
    renshe_oss_access_key_secret: SecretStr
    quiz_oss_endpoint: str
    quiz_oss_bucket: str
    quiz_oss_access_key_id: SecretStr
    quiz_oss_access_key_secret: SecretStr

    recovery_oss_endpoint: str
    recovery_oss_bucket: str
    recovery_oss_prefix: str = "wemini-recovery"
    recovery_oss_access_key_id: SecretStr
    recovery_oss_access_key_secret: SecretStr
    recovery_public_key_pem: SecretStr

    @field_validator("api_origin")
    @classmethod
    def validate_api_origin(cls, value: str) -> str:
        return _https_origin(value, field_name="api_origin")

    @field_validator("admin_origin")
    @classmethod
    def validate_admin_origin(cls, value: str) -> str:
        return _https_origin(value, field_name="admin_origin")

    @field_validator(
        "renshe_oss_endpoint",
        "quiz_oss_endpoint",
        "recovery_oss_endpoint",
    )
    @classmethod
    def validate_endpoint(cls, value: str, info) -> str:
        return _https_endpoint(value, field_name=info.field_name)

    @field_validator(
        "renshe_oss_bucket",
        "quiz_oss_bucket",
        "recovery_oss_bucket",
    )
    @classmethod
    def validate_bucket(cls, value: str, info) -> str:
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

    @field_validator("recovery_oss_prefix")
    @classmethod
    def validate_recovery_prefix(cls, value: str) -> str:
        cleaned = _clean_single_line(value, field_name="recovery_oss_prefix", max_length=128)
        if cleaned.startswith("/") or cleaned.endswith("/") or ".." in cleaned.split("/"):
            raise ValueError("recovery_oss_prefix is invalid")
        return cleaned

    @model_validator(mode="after")
    def validate_modes_and_origins(self) -> "BootstrapConfigureRequest":
        if self.api_origin == self.admin_origin:
            raise ValueError("api_origin and admin_origin must be different")
        if self.deployment_mode == "external":
            if self.postgres_host == "db":
                raise ValueError("external deployment requires an explicit postgres_host")
            if self.postgres_password is None or not self.postgres_password.get_secret_value():
                raise ValueError("external deployment requires postgres_password")
            if self.redis_url is None or not self.redis_url.get_secret_value():
                raise ValueError("external deployment requires redis_url")
            redis_scheme = urlsplit(self.redis_url.get_secret_value()).scheme
            if redis_scheme not in {"redis", "rediss"}:
                raise ValueError("redis_url must use redis:// or rediss://")
        return self


class BootstrapAdminRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=64)
    password: SecretStr

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        cleaned = _clean_single_line(value, field_name="username", max_length=64)
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", cleaned):
            raise ValueError("username contains unsupported characters")
        return cleaned

    @model_validator(mode="after")
    def validate_password(self) -> "BootstrapAdminRequest":
        password = self.password.get_secret_value()
        if len(password) < 12 or len(password) > 256:
            raise ValueError("password must contain 12 to 256 characters")
        if "\x00" in password or "\r" in password or "\n" in password:
            raise ValueError("password contains unsupported control characters")
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
