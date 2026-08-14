"""Regression tests for BE-16 audit privacy and BE-18 dependency diagnostics."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from Crypto.PublicKey import RSA
from httpx import ASGITransport, AsyncClient

from app.main import _dependency_checks, ready
from app.port.config import Settings, settings
from app.services.dependency_health import is_ready
from app.services.dependency_health import (
    enrich_quiz_oss_probe,
    inspect_quiz_oss_configuration,
    inspect_wechat_payment_configuration,
    probe_quiz_oss,
)
from app.utils.audit import (
    REDACTED,
    redact_sensitive_text,
    sanitize_audit_summary,
    sanitize_audit_value,
)
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


def test_audit_values_never_persist_object_keys_or_signed_urls() -> None:
    sanitized = sanitize_audit_value(
        {
            "source_object_key": "quiz-imports/private-source.json",
            "report_object_key": "quiz-imports/private-report.json",
            "signed_url": "https://private.example/download?signature=secret",
            "created_count": 12,
        }
    )

    assert sanitized == {
        "source_object_key": REDACTED,
        "report_object_key": REDACTED,
        "signed_url": REDACTED,
        "created_count": 12,
    }


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
    monkeypatch.setattr(
        "app.main._database_probe", AsyncMock(return_value={"status": "ok"})
    )
    monkeypatch.setattr("app.main._check_redis", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "app.main.inspect_oss_configuration",
        lambda: {"status": "ok", "configured": True, "mode": "local"},
    )
    monkeypatch.setattr(
        "app.main.inspect_quiz_oss_configuration",
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
    assert checks["quiz_oss"] == "ok"
    assert details["quiz_oss"]["mode"] == "local"
    assert details["wechat_payment"]["status"] == "disabled"


@pytest.mark.asyncio
async def test_dependency_checks_reject_unwritable_local_quiz_storage(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.main._database_probe", AsyncMock(return_value={"status": "ok"})
    )
    monkeypatch.setattr("app.main._check_redis", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "app.main.inspect_oss_configuration",
        lambda: {"status": "ok", "configured": True, "mode": "local"},
    )
    monkeypatch.setattr(
        "app.main.inspect_quiz_oss_configuration",
        lambda: {"status": "ok", "configured": True, "mode": "local"},
    )
    monkeypatch.setattr(
        "app.main.enrich_quiz_oss_probe",
        AsyncMock(
            return_value={
                "status": "unavailable",
                "configured": True,
                "mode": "local",
                "probe": "unavailable",
                "reason": "local_storage_not_writable",
            }
        ),
    )
    monkeypatch.setattr(
        "app.main.inspect_wechat_login_configuration",
        lambda: {"status": "ok", "configured": True},
    )
    monkeypatch.setattr(
        "app.main.inspect_wechat_payment_configuration",
        lambda: {"status": "disabled", "configured": False, "required": False},
    )

    checks, details = await _dependency_checks(probe_external=True)

    assert checks["quiz_oss"] == "unavailable"
    assert details["quiz_oss"]["reason"] == "local_storage_not_writable"


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
                    "quiz_oss": "ok",
                    "wechat_login": "ok",
                    "wechat_payment": "ok",
                },
                {"oss": {"status": "unavailable", "reason": "bucket_probe_failed"}},
            )
        ),
    )
    monkeypatch.setattr(
        "app.main.read_quiz_task_snapshot",
        AsyncMock(
            return_value={
                "source": "process",
                "heartbeat_at": "2026-08-12T00:00:00+00:00",
                "processors": {},
            }
        ),
    )
    monkeypatch.setattr("app.main.quiz_task_snapshot_ready", lambda snapshot: True)
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
            "quiz_oss": "ok",
            "quiz_worker": "ok",
            "wechat_login": "ok",
            "wechat_payment": "disabled",
        }
        assert is_ready(checks) is False
        checks["wechat_payment"] = "ok"
        assert is_ready(checks) is True
    finally:
        settings.APP_ENV = previous


def test_quiz_oss_configuration_is_independent_from_renshe_storage(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "RENSHE_STORAGE_TYPE", "local")
    monkeypatch.setattr(settings, "QUIZ_IMPORT_STORAGE_TYPE", "aliyun_oss")
    monkeypatch.setattr(settings, "QUIZ_OSS_ENDPOINT", "https://oss.example")
    monkeypatch.setattr(settings, "QUIZ_OSS_BUCKET", "")
    monkeypatch.setattr(settings, "QUIZ_OSS_ACCESS_KEY_ID", "quiz-key")
    monkeypatch.setattr(settings, "QUIZ_OSS_ACCESS_KEY_SECRET", "quiz-secret")

    result = inspect_quiz_oss_configuration()

    assert result["status"] == "unavailable"
    assert result["mode"] == "aliyun_oss"
    assert result["missing"] == ["QUIZ_OSS_BUCKET"]


@pytest.mark.asyncio
async def test_quiz_oss_probe_requires_private_bucket_acl(monkeypatch) -> None:
    async def run_inline(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("app.services.dependency_health.asyncio.to_thread", run_inline)

    class _Bucket:
        def __init__(self, acl: str) -> None:
            self.acl = acl

        def get_bucket_acl(self):
            return SimpleNamespace(status=200, acl=self.acl)

    monkeypatch.setattr(settings, "QUIZ_IMPORT_STORAGE_TYPE", "aliyun_oss")
    monkeypatch.setattr(settings, "QUIZ_OSS_ENDPOINT", "https://oss.example")
    monkeypatch.setattr(settings, "QUIZ_OSS_BUCKET", "quiz-private")
    monkeypatch.setattr(settings, "QUIZ_OSS_ACCESS_KEY_ID", "quiz-key")
    monkeypatch.setattr(settings, "QUIZ_OSS_ACCESS_KEY_SECRET", "quiz-secret")
    fake_oss2 = SimpleNamespace(
        Auth=lambda *_args: object(),
        Bucket=lambda *_args: _Bucket("public-read"),
    )
    monkeypatch.setitem(sys.modules, "oss2", fake_oss2)
    assert await probe_quiz_oss(timeout_seconds=1) is False

    fake_oss2.Bucket = lambda *_args: _Bucket("private")
    assert await probe_quiz_oss(timeout_seconds=1) is True


@pytest.mark.asyncio
async def test_local_quiz_storage_probe_creates_and_removes_file(
    monkeypatch, tmp_path
) -> None:
    async def run_inline(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("app.services.dependency_health.asyncio.to_thread", run_inline)
    monkeypatch.setattr(settings, "QUIZ_IMPORT_STORAGE_TYPE", "local")
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "QUIZ_OSS_PREFIX", "quiz-imports")

    result = await enrich_quiz_oss_probe(inspect_quiz_oss_configuration())

    assert result == {
        "status": "ok",
        "configured": True,
        "mode": "local",
        "probe": "ok",
    }
    target = tmp_path / "private" / "quiz-imports"
    assert target.is_dir()
    assert list(target.iterdir()) == []


@pytest.mark.asyncio
async def test_local_quiz_storage_probe_reports_non_sensitive_write_failure(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "QUIZ_IMPORT_STORAGE_TYPE", "local")
    monkeypatch.setattr(
        "app.services.dependency_health._probe_local_quiz_storage",
        lambda: False,
    )

    result = await enrich_quiz_oss_probe(inspect_quiz_oss_configuration())

    assert result["status"] == "unavailable"
    assert result["probe"] == "unavailable"
    assert result["reason"] == "local_storage_not_writable"


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
        "WECHAT_PAY_PUBLIC_KEY": "public-key",
        "WECHAT_PAY_PUBLIC_KEY_ID": "PUB_KEY_ID_test",
        "RENSHE_STORAGE_TYPE": "aliyun_oss",
        "ALIYUN_OSS_ENDPOINT": "https://oss.example",
        "ALIYUN_OSS_BUCKET": "private-bucket",
        "ALIYUN_OSS_ACCESS_KEY_ID": "access-key",
        "ALIYUN_OSS_ACCESS_KEY_SECRET": "access-secret",
        "QUIZ_IMPORT_STORAGE_TYPE": "aliyun_oss",
        "QUIZ_EMBEDDED_WORKER_ENABLED": False,
        "QUIZ_OSS_ENDPOINT": "https://oss.example",
        "QUIZ_OSS_BUCKET": "quiz-bucket",
        "QUIZ_OSS_ACCESS_KEY_ID": "quiz-key",
        "QUIZ_OSS_ACCESS_KEY_SECRET": "quiz-secret",
        "QUIZ_METRICS_BEARER_TOKEN": "quiz-metrics-token-that-is-long-enough",
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


def test_settings_load_secrets_from_read_only_files(tmp_path, monkeypatch) -> None:
    for name in ("JWT_SECRET", "PII_HASH_KEY", "REDIS_URL"):
        monkeypatch.delenv(name, raising=False)
    jwt_file = tmp_path / "jwt"
    pii_file = tmp_path / "pii"
    redis_file = tmp_path / "redis"
    wechat_pay_public_key_file = tmp_path / "wechat-pay-public-key"
    jwt_file.write_text("j" * 40 + "\n", encoding="utf-8")
    pii_file.write_text("p" * 40 + "\n", encoding="utf-8")
    redis_file.write_text("redis://redis.internal:6379/0\n", encoding="utf-8")
    wechat_pay_public_key_file.write_text("public-key\n", encoding="utf-8")

    file_settings = Settings(
        _env_file=None,
        APP_ENV="test",
        JWT_SECRET_FILE=str(jwt_file),
        PII_HASH_KEY_FILE=str(pii_file),
        REDIS_URL_FILE=str(redis_file),
        WECHAT_PAY_PUBLIC_KEY_FILE=str(wechat_pay_public_key_file),
    )

    assert file_settings.JWT_SECRET == "j" * 40
    assert file_settings.PII_HASH_KEY == "p" * 40
    assert file_settings.REDIS_URL == "redis://redis.internal:6379/0"
    assert file_settings.WECHAT_PAY_PUBLIC_KEY == "public-key"
    assert "JWT_SECRET_FILE" not in file_settings.model_dump()
    assert "REDIS_URL_FILE" not in file_settings.model_dump()
    assert "WECHAT_PAY_PUBLIC_KEY_FILE" not in file_settings.model_dump()


def test_wechat_payment_health_requires_public_key_id_and_rsa_public_key(
    monkeypatch,
) -> None:
    wechat_pay_key = RSA.generate(2048)
    configured_values = {
        "APP_ENV": "test",
        "WECHAT_PAY_ENABLED": True,
        "WECHAT_PAY_API_VERSION": "v3",
        "WECHAT_PAY_MCHID": "1900000001",
        "WECHAT_PAY_APPID": "wx-test-appid",
        "WECHAT_PAY_CERT_SERIAL_NO": "merchant-serial",
        "WECHAT_PAY_PRIVATE_KEY": "merchant-private-key",
        "WECHAT_PAY_API_V3_KEY": "0123456789abcdef0123456789abcdef",
        "WECHAT_PAY_PUBLIC_KEY": wechat_pay_key.public_key()
        .export_key()
        .decode("ascii"),
        "WECHAT_PAY_PUBLIC_KEY_ID": (
            "PUB_KEY_ID_0000000000000024101100397200000006"
        ),
        "WECHAT_PAY_NOTIFY_URL": "https://example.test/payment/callback",
        "WECHAT_PAY_REFUND_NOTIFY_URL": (
            "https://example.test/payment/refund-callback"
        ),
    }
    for name, value in configured_values.items():
        monkeypatch.setattr(settings, name, value)

    assert inspect_wechat_payment_configuration() == {
        "status": "ok",
        "configured": True,
        "required": True,
        "api": "v3",
    }

    monkeypatch.setattr(settings, "WECHAT_PAY_PUBLIC_KEY_ID", "wrong-id")
    assert inspect_wechat_payment_configuration()["reason"] == (
        "invalid_wechat_pay_public_key_id"
    )

    monkeypatch.setattr(
        settings,
        "WECHAT_PAY_PUBLIC_KEY_ID",
        configured_values["WECHAT_PAY_PUBLIC_KEY_ID"],
    )
    monkeypatch.setattr(
        settings,
        "WECHAT_PAY_PUBLIC_KEY",
        wechat_pay_key.export_key().decode("ascii"),
    )
    assert inspect_wechat_payment_configuration()["reason"] == (
        "invalid_wechat_pay_public_key"
    )


def test_settings_reject_ambiguous_or_empty_secret_files(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("JWT_SECRET", raising=False)
    empty_file = tmp_path / "empty"
    empty_file.write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError, match="secret file for JWT_SECRET is empty"):
        Settings(
            _env_file=None,
            APP_ENV="test",
            JWT_SECRET_FILE=str(empty_file),
        )

    populated = tmp_path / "populated"
    populated.write_text("f" * 40, encoding="utf-8")
    with pytest.raises(ValueError, match="mutually exclusive"):
        Settings(
            _env_file=None,
            APP_ENV="test",
            JWT_SECRET="d" * 40,
            JWT_SECRET_FILE=str(populated),
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
