"""Regression tests for BE-16 audit privacy and BE-18 dependency diagnostics."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import _dependency_checks, ready
from app.port.config import Settings, settings
from app.services.dependency_health import is_ready
from app.utils.audit import REDACTED, redact_sensitive_text, sanitize_audit_summary
from app.main import app
from app.services.renshe_source import (
    delete_unreferenced_source_keys,
    profile_source_keys,
)
from app.services.admin_user import AdminUserService
from app.schemas.admin import AdminProfileUpdate
from app.domain.renshe.src.index import RensheAuditLog


def test_audit_summary_redacts_known_pii_keys_and_embedded_patterns() -> None:
    summary = sanitize_audit_summary(
        {
            "id_card_number": "11010519491231002X",
            "contact_phone": "13800138000",
            "nested": ["phone=13800138000", {"openid": "wx-secret"}],
            "candidate_total": 150,
        }
    )

    assert summary is not None
    assert summary["id_card_number"] == REDACTED
    assert summary["contact_phone"] == REDACTED
    assert summary["nested"][0] == "phone=[REDACTED]"
    assert summary["nested"][1]["openid"] == REDACTED
    assert summary["candidate_total"] == 150


def test_free_form_diagnostics_redact_tokens_and_pii() -> None:
    value = redact_sensitive_text(
        "Bearer eyJhbGciOiJIUzI1NiJ9; id=11010519491231002X; phone=13800138000; "
        "url=https://bucket.oss-cn-hangzhou.aliyuncs.com/renshe/source/1/a.jpg"
    )
    assert value == (
        "Bearer [REDACTED]; id=[REDACTED]; phone=[REDACTED]; url=[REDACTED]"
    )


def test_profile_source_keys_collects_only_non_empty_current_materials() -> None:
    realname = SimpleNamespace(
        id_card_front_oss="renshe/source/1/front.jpg",
        id_card_back_oss="",
        avatar_oss=None,
    )
    student = SimpleNamespace(
        student_card_oss="renshe/source/1/student.jpg",
        enrollment_pdf_oss="renshe/source/1/enrollment.pdf",
        degree_cert_oss=None,
    )
    assert profile_source_keys(realname, student) == {
        "renshe/source/1/front.jpg",
        "renshe/source/1/student.jpg",
        "renshe/source/1/enrollment.pdf",
    }


def test_deleted_source_sentinel_is_never_treated_as_an_object_key() -> None:
    student = SimpleNamespace(
        student_card_oss="deleted",
        enrollment_pdf_oss="deleted:legacy",
        degree_cert_oss="renshe/source/1/education.jpg",
    )
    assert profile_source_keys(student=student) == {
        "renshe/source/1/education.jpg"
    }


@pytest.mark.asyncio
async def test_source_cleanup_can_surface_oss_failure_for_retention_retry(monkeypatch) -> None:
    class _DbContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("app.adapter.database.get_db_ctx", lambda: _DbContext())
    monkeypatch.setattr(
        "app.services.renshe_source.find_unreferenced_source_keys",
        AsyncMock(return_value={"renshe/source/1/orphan.jpg"}),
    )
    storage = SimpleNamespace(
        delete_many=AsyncMock(side_effect=RuntimeError("oss unavailable"))
    )
    with pytest.raises(RuntimeError, match="oss unavailable"):
        await delete_unreferenced_source_keys(
            storage,
            ["renshe/source/1/orphan.jpg"],
            raise_on_error=True,
        )
    storage.delete_many.assert_awaited_once_with(["renshe/source/1/orphan.jpg"])


@pytest.mark.asyncio
async def test_dependency_checks_keep_legacy_shape_and_add_safe_details(monkeypatch) -> None:
    monkeypatch.setattr("app.main._check_db", AsyncMock(return_value=True))
    monkeypatch.setattr("app.main._check_redis", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "app.main.inspect_oss_configuration",
        lambda: {"status": "ok", "configured": True, "mode": "local"},
    )
    monkeypatch.setattr(
        "app.main.inspect_wechat_login_configuration",
        lambda: {"status": "ok", "configured": True},
    )
    monkeypatch.setattr(
        "app.main.inspect_wechat_payment_configuration",
        lambda: {"status": "disabled", "configured": False, "required": False},
    )

    checks, details = await _dependency_checks(probe_external=False)

    assert checks["database"] == "ok"
    assert checks["redis"] == "ok"
    assert checks["oss"] == "ok"
    assert details["wechat_payment"]["status"] == "disabled"


@pytest.mark.asyncio
async def test_ready_returns_503_when_required_dependency_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main._dependency_checks",
        AsyncMock(
            return_value=(
                {
                    "database": "ok",
                    "redis": "ok",
                    "oss": "unavailable",
                    "wechat_login": "ok",
                    "wechat_payment": "ok",
                },
                {"oss": {"status": "unavailable", "reason": "bucket_probe_failed"}},
            )
        ),
    )
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


def test_readiness_requires_production_wechat_and_payment() -> None:
    previous = settings.APP_ENV
    try:
        settings.APP_ENV = "production"
        checks = {
            "database": "ok",
            "redis": "ok",
            "oss": "ok",
            "wechat_login": "ok",
            "wechat_payment": "disabled",
        }
        assert is_ready(checks) is False
        checks["wechat_payment"] = "ok"
        assert is_ready(checks) is True
    finally:
        settings.APP_ENV = previous


def test_production_settings_require_explicit_wechat_v3_configuration() -> None:
    values = {
        "_env_file": None,
        "APP_ENV": "production",
        "APP_DEBUG": False,
        "JWT_SECRET": "be-test-jwt-secret-that-is-at-least-32-characters",
        "PII_HASH_KEY": "be-test-pii-secret-that-is-at-least-32-characters",
        "DB_PASSWORD": "db-password",
        "REDIS_URL": "rediss://redis.example/0",
        "WECHAT_APPID": "wx-appid",
        "WECHAT_SECRET": "wx-secret",
        "WECHAT_PAY_ENABLED": True,
        "WECHAT_PAY_API_VERSION": "v3",
        "WECHAT_PAY_MCHID": "mch-id",
        "WECHAT_PAY_APPID": "wx-appid",
        "WECHAT_PAY_NOTIFY_URL": "https://example.test/wechat/notify",
        "WECHAT_PAY_REFUND_NOTIFY_URL": "https://example.test/wechat/refund-notify",
        "WECHAT_PAY_CERT_SERIAL_NO": "serial",
        "WECHAT_PAY_PRIVATE_KEY": "private-key",
        "WECHAT_PAY_API_V3_KEY": "0123456789abcdef0123456789abcdef",
        "WECHAT_PAY_PLATFORM_CERTIFICATE": "platform-cert",
        "WECHAT_PAY_PLATFORM_CERT_SERIAL_NO": "platform-serial",
        "RENSHE_STORAGE_TYPE": "aliyun_oss",
        "ALIYUN_OSS_ENDPOINT": "https://oss.example",
        "ALIYUN_OSS_BUCKET": "private-bucket",
        "ALIYUN_OSS_ACCESS_KEY_ID": "access-key",
        "ALIYUN_OSS_ACCESS_KEY_SECRET": "access-secret",
        "QUIZ_IMPORT_STORAGE_TYPE": "aliyun_oss",
        "QUIZ_OSS_ENDPOINT": "https://oss.example",
        "QUIZ_OSS_BUCKET": "quiz-bucket",
        "QUIZ_OSS_ACCESS_KEY_ID": "quiz-key",
        "QUIZ_OSS_ACCESS_KEY_SECRET": "quiz-secret",
    }
    valid = Settings(**values)
    assert valid.WECHAT_PAY_API_VERSION == "v3"
    assert valid.RENSHE_CLEANUP_RETENTION_DAYS == 30

    with pytest.raises(
        ValueError,
        match="RENSHE_CLEANUP_RETENTION_DAYS must remain 30 in production",
    ):
        Settings(**{**values, "RENSHE_CLEANUP_RETENTION_DAYS": 29})

    values.pop("WECHAT_PAY_API_V3_KEY")
    with pytest.raises(ValueError, match="WECHAT_PAY_API_V3_KEY"):
        Settings(**values)


def test_database_url_driver_is_derived_when_only_one_complete_url_is_given() -> None:
    common = {
        "_env_file": None,
        "APP_ENV": "test",
        "JWT_SECRET": "be-test-jwt-secret-that-is-at-least-32-characters",
        "PII_HASH_KEY": "be-test-pii-secret-that-is-at-least-32-characters",
    }
    async_settings = Settings(
        **common,
        DATABASE_URL="postgresql://user:p%40ss@db.example:3306/app",
        DATABASE_URL_SYNC="",
    )
    assert async_settings.DATABASE_URL.startswith("postgresql+asyncpg://")
    assert async_settings.DATABASE_URL_SYNC == "postgresql://user:p%40ss@db.example:3306/app"

    sync_settings = Settings(
        **common,
        DATABASE_URL="",
        DATABASE_URL_SYNC="postgresql+psycopg2://user:p%40ss@db.example:3306/app",
    )
    assert sync_settings.DATABASE_URL == "postgresql+asyncpg://user:p%40ss@db.example:3306/app"
    assert sync_settings.DATABASE_URL_SYNC == (
        "postgresql+psycopg2://user:p%40ss@db.example:3306/app"
    )


@pytest.mark.asyncio
async def test_admin_profile_update_writes_pii_safe_audit(monkeypatch) -> None:
    class _Result:
        def scalar_one_or_none(self):
            return profile

    class _Db:
        def __init__(self):
            self.added = []
            self.commits = 0

        async def get(self, model, object_id):
            return user

        async def execute(self, statement):
            return _Result()

        async def scalar(self, statement):
            return None

        def add(self, value):
            self.added.append(value)

        async def commit(self):
            self.commits += 1

    user = SimpleNamespace(id=12, phone="13800138000")
    profile = SimpleNamespace(
        user_id=12,
        nickname="old",
        email=None,
        province=None,
        city=None,
        address=None,
    )
    db = _Db()

    class _Context:
        async def __aenter__(self):
            return db

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("app.services.admin_user.get_db_ctx", lambda: _Context())
    monkeypatch.setattr(
        AdminUserService,
        "get_user_profile",
        AsyncMock(return_value=SimpleNamespace(id=12)),
    )

    result = await AdminUserService().update_user_profile(
        12,
        AdminProfileUpdate(nickname="new", phone="13900139000"),
        actor_id=7,
    )

    assert result.id == 12
    assert db.commits == 1
    audit = next(item for item in db.added if isinstance(item, RensheAuditLog))
    assert audit.action == "user.profile.update"
    assert audit.actor_id == 7
    assert audit.summary == {"changed_fields": ["nickname", "phone"]}
