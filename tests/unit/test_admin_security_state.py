from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.adapter import security
from app.middleware import auth as auth_middleware
from app.port.exceptions import BusinessException, ForbiddenException, UnauthorizedException
from app.schemas.admin import (
    AdminChangePasswordRequest,
    AdminLoginResponse,
    AdminReauthResponse,
)
from app.schemas.admin_settings import (
    AdminSettingsTemporaryPasswordResponse,
    AdminSettingsUserCreate,
    AdminSettingsUserListItem,
)
from app.services import admin_auth as admin_auth_module
from app.services import admin_security_audit as admin_security_audit_module
from app.services.admin_auth import AdminAuthService
from app.services.admin_security_audit import AdminAuditContext, AdminSecurityAuditService


class _Result:
    def __init__(self, value=None, values=None):
        self.value = value
        self.values = list(values or [])

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self.values


class _FakeDB:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.added = []
        self.commits = 0

    async def execute(self, _statement):
        return self.results.pop(0)

    async def get(self, _model, _identifier):
        return self.results.pop(0).value

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        return None

    async def commit(self):
        self.commits += 1

    async def refresh(self, _instance):
        return None


def _db_context(db):
    @asynccontextmanager
    async def _context():
        yield db

    return _context


def _admin(**overrides):
    now = datetime.now(timezone.utc)
    values = {
        "id": 7,
        "username": "quiz.operator",
        "display_name": "题库运营",
        "password_hash": "hash:current",
        "role": "quiz_admin",
        "is_active": True,
        "must_change_password": False,
        "auth_version": 3,
        "failed_login_attempts": 0,
        "locked_until": None,
        "last_login_at": None,
        "last_login_ip": None,
        "password_changed_at": now,
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_admin_token_has_unique_session_and_frozen_security_claims():
    first = security.create_admin_access_token(
        7,
        "quiz.operator",
        "quiz_admin",
        auth_version=4,
        session_mode="restricted",
    )
    second = security.create_admin_access_token(
        7,
        "quiz.operator",
        "quiz_admin",
        auth_version=4,
        session_mode="restricted",
    )

    first_payload = security.decode_access_token(first)
    second_payload = security.decode_access_token(second)
    assert first_payload["auth_version"] == 4
    assert first_payload["session_mode"] == "restricted"
    assert first_payload["exp"] - first_payload["iat"] == 120 * 60
    assert first_payload["jti"] != second_payload["jti"]


@pytest.mark.asyncio
async def test_reauth_is_server_side_and_bound_to_admin_session(monkeypatch):
    stored = {}

    async def _setex(key, ttl, value):
        stored[key] = (ttl, value)
        return True

    async def _get(key):
        entry = stored.get(key)
        return entry[1] if entry else None

    monkeypatch.setattr(security, "redis_setex_safe", _setex)
    monkeypatch.setattr(security, "redis_get_safe", _get)
    token = await security.create_admin_reauth_token(
        admin_id=7, jti="browser-session", auth_version=3
    )
    assert token is not None
    assert token not in next(iter(stored))
    assert next(iter(stored.values()))[0] == 600
    assert await security.validate_admin_reauth_token(
        token, admin_id=7, jti="browser-session", auth_version=3
    )
    assert not await security.validate_admin_reauth_token(
        token, admin_id=7, jti="different-session", auth_version=3
    )
    assert not await security.validate_admin_reauth_token(
        token, admin_id=7, jti="browser-session", auth_version=4
    )


@pytest.mark.asyncio
async def test_logout_revocation_uses_digest_key_and_reports_store_failure(monkeypatch):
    writes = []

    async def _store(key, ttl, value):
        writes.append((key, ttl, value))
        return True

    monkeypatch.setattr(security, "redis_setex_safe", _store)
    token = security.create_admin_access_token(
        7, "quiz.operator", "quiz_admin", auth_version=3
    )
    assert await security.revoke_token(token) is True
    assert writes[0][0].startswith("jwt:blacklist:sha256:")
    assert token not in writes[0][0]

    async def _unavailable(_key, _ttl, _value):
        return False

    monkeypatch.setattr(security, "redis_setex_safe", _unavailable)
    assert await security.revoke_token(token) is False


@pytest.mark.asyncio
async def test_admin_revocation_check_fails_closed_when_redis_is_unavailable(
    monkeypatch,
):
    token = security.create_admin_access_token(
        7, "quiz.operator", "quiz_admin", auth_version=3
    )

    async def _unavailable(_key):
        raise security.RedisUnavailableError("redis unavailable")

    monkeypatch.setattr(security, "redis_get_required", _unavailable)
    assert await security.is_token_revoked(token) is True


@pytest.mark.asyncio
async def test_user_revocation_check_keeps_existing_cache_fallback(monkeypatch):
    token = security.create_access_token(5, "openid-5")

    async def _cache_miss(_key):
        return None

    monkeypatch.setattr(security, "redis_get_safe", _cache_miss)
    assert await security.is_token_revoked(token) is False


@pytest.mark.asyncio
async def test_admin_auth_version_and_session_mode_are_checked(monkeypatch):
    admin = _admin(auth_version=9, must_change_password=True)
    token = security.create_admin_access_token(
        admin.id,
        admin.username,
        admin.role,
        auth_version=9,
        session_mode="restricted",
    )
    db = _FakeDB([_Result(admin)])

    async def _not_revoked(_token):
        return False

    monkeypatch.setattr(auth_middleware, "is_token_revoked", _not_revoked)
    principal = await auth_middleware.get_current_admin(
        authorization=f"Bearer {token}", db=db
    )
    assert principal._session_mode == "restricted"
    with pytest.raises(ForbiddenException):
        await auth_middleware.require_normal_admin(admin=principal)

    stale = security.create_admin_access_token(
        admin.id,
        admin.username,
        admin.role,
        auth_version=8,
        session_mode="restricted",
    )
    db.results.append(_Result(admin))
    with pytest.raises(UnauthorizedException):
        await auth_middleware.get_current_admin(
            authorization=f"Bearer {stale}", db=db
        )


def test_admin_account_schemas_normalize_and_reject_legacy_credentials():
    created = AdminSettingsUserCreate(
        username="  Quiz.Operator  ", display_name="  题库运营  ", role="quiz_admin"
    )
    assert created.username == "quiz.operator"
    assert created.display_name == "题库运营"
    with pytest.raises(ValidationError):
        AdminSettingsUserCreate(
            username="quiz.operator",
            display_name="题库运营",
            password="caller-selected-secret-42",
        )
    with pytest.raises(ValidationError):
        AdminChangePasswordRequest(
            current_password="current-value",
            new_password="NextSecureValue2026",
            confirm_password="DifferentSecureValue2026",
        )


def test_one_time_credentials_are_not_exposed_by_model_repr():
    now = datetime.now(timezone.utc)
    admin = AdminSettingsUserListItem(
        id=7,
        username="quiz.operator",
        display_name="题库运营",
        role="quiz_admin",
        is_active=True,
        must_change_password=True,
        created_at=now,
        updated_at=now,
    )
    temporary = AdminSettingsTemporaryPasswordResponse(
        admin=admin,
        temporary_password="OneTimeValue2026",
    )
    reauth = AdminReauthResponse(reauth_token="reauth-secret-value")
    assert "OneTimeValue2026" not in repr(temporary)
    assert "reauth-secret-value" not in repr(reauth)


def test_login_response_cannot_fall_back_to_legacy_full_permissions():
    assert AdminLoginResponse.model_fields["permissions"].is_required()


def test_password_policy_and_generated_temporary_password():
    with pytest.raises(BusinessException):
        AdminAuthService.validate_password(
            "Quiz.Operator2026", username="quiz.operator"
        )
    with pytest.raises(BusinessException):
        AdminAuthService.validate_password("onlylettersvalue", username="quiz.operator")

    temporary = AdminAuthService.generate_temporary_password(
        username="quiz.operator"
    )
    assert 12 <= len(temporary) <= 128
    assert any(character.isalpha() for character in temporary)
    assert any(character.isdigit() for character in temporary)
    assert "quiz.operator" not in temporary.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("admin", "expected_reason"),
    [
        (None, "invalid_credentials"),
        (_admin(is_active=False), "account_inactive"),
        (
            _admin(
                locked_until=datetime.now(timezone.utc) + timedelta(minutes=10)
            ),
            "account_locked",
        ),
    ],
)
async def test_login_does_not_reveal_unknown_inactive_or_locked_accounts(
    monkeypatch,
    admin,
    expected_reason,
):
    db = _FakeDB([_Result(admin)])
    monkeypatch.setattr(admin_auth_module, "get_db_ctx", _db_context(db))
    monkeypatch.setattr(
        AdminAuthService,
        "verify_password",
        staticmethod(lambda _password, _stored_hash: False),
    )

    with pytest.raises(UnauthorizedException) as exc_info:
        await AdminAuthService().login("QUIZ.OPERATOR", "incorrect-value")

    assert exc_info.value.code == 40100
    assert exc_info.value.message == "账号或密码错误"
    assert db.commits == 1
    audits = [row for row in db.added if getattr(row, "action", None) == "auth.admin_login"]
    assert len(audits) == 1
    assert audits[0].result == "failed"
    assert audits[0].reason_code == expected_reason


@pytest.mark.asyncio
async def test_expired_lock_is_audited_and_successful_login_clears_failures(
    monkeypatch,
):
    admin = _admin(
        password_hash="hash:current",
        failed_login_attempts=5,
        locked_until=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    db = _FakeDB([_Result(admin)])
    monkeypatch.setattr(admin_auth_module, "get_db_ctx", _db_context(db))
    monkeypatch.setattr(
        AdminAuthService,
        "verify_password",
        staticmethod(lambda password, password_hash: (
            password == "current-value" and password_hash == "hash:current"
        )),
    )

    result = await AdminAuthService().login(
        admin.username,
        "current-value",
        context=AdminAuditContext(source_ip="100.64.0.8"),
    )

    assert result.session_mode == "normal"
    assert result.must_change_password is False
    assert admin.failed_login_attempts == 0
    assert admin.locked_until is None
    assert admin.last_login_ip == "100.64.0.8"
    assert db.commits == 1
    assert [
        row.action for row in db.added if getattr(row, "action", None)
    ] == ["admin_account.auto_unlock", "auth.admin_login"]


@pytest.mark.asyncio
async def test_fifth_failed_login_locks_for_fifteen_minutes(monkeypatch):
    admin = _admin(password_hash="stored-hash")
    db = _FakeDB([_Result(admin) for _ in range(5)])
    monkeypatch.setattr(admin_auth_module, "get_db_ctx", _db_context(db))
    monkeypatch.setattr(
        AdminAuthService,
        "verify_password",
        staticmethod(lambda _password, _stored_hash: False),
    )
    service = AdminAuthService()

    for _ in range(5):
        with pytest.raises(UnauthorizedException) as exc_info:
            await service.login("QUIZ.OPERATOR", "incorrect-value")
        assert exc_info.value.code == 40100
        assert exc_info.value.message == "账号或密码错误"

    assert admin.failed_login_attempts == 5
    assert admin.locked_until is not None
    remaining = admin.locked_until - datetime.now(timezone.utc)
    assert timedelta(minutes=14, seconds=50) < remaining <= timedelta(minutes=15)
    assert db.commits == 5
    assert any(
        getattr(row, "action", None) == "admin_account.lock" for row in db.added
    )


@pytest.mark.asyncio
async def test_password_change_increments_version_and_clears_restrictions(monkeypatch):
    admin = _admin(
        password_hash="hash:old",
        must_change_password=True,
        failed_login_attempts=4,
        locked_until=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    db = _FakeDB(
        [
            _Result(admin),
            _Result(values=[]),
            _Result(values=[]),
        ]
    )
    monkeypatch.setattr(admin_auth_module, "get_db_ctx", _db_context(db))
    monkeypatch.setattr(
        AdminAuthService,
        "verify_password",
        staticmethod(
            lambda password, password_hash: password == "current-value"
            and password_hash == "hash:old"
        ),
    )
    monkeypatch.setattr(
        AdminAuthService,
        "hash_password",
        staticmethod(lambda _password: "hash:new"),
    )

    await AdminAuthService().change_password(
        admin_id=admin.id,
        expected_auth_version=admin.auth_version,
        current_password="current-value",
        new_password="NextSecureValue2026",
    )

    assert admin.password_hash == "hash:new"
    assert admin.auth_version == 4
    assert admin.must_change_password is False
    assert admin.failed_login_attempts == 0
    assert admin.locked_until is None


@pytest.mark.asyncio
async def test_reauth_does_not_issue_grant_when_success_audit_commit_fails(
    monkeypatch,
):
    admin = _admin(
        role="super_admin",
        password_hash="hash:super",
        must_change_password=False,
    )
    db = _FakeDB([_Result(admin)])
    monkeypatch.setattr(admin_auth_module, "get_db_ctx", _db_context(db))
    monkeypatch.setattr(
        AdminAuthService,
        "verify_password",
        staticmethod(lambda password, password_hash: password == "current-value"),
    )
    create_grant = AsyncMock(return_value="must-not-be-issued")
    monkeypatch.setattr(admin_auth_module, "create_admin_reauth_token", create_grant)
    service = AdminAuthService()
    service.audit = SimpleNamespace(
        record=AsyncMock(side_effect=RuntimeError("audit unavailable"))
    )

    with pytest.raises(RuntimeError, match="audit unavailable"):
        await service.reauthenticate(
            admin_id=admin.id,
            expected_auth_version=admin.auth_version,
            jti="browser-session",
            password="current-value",
        )

    create_grant.assert_not_awaited()


@pytest.mark.asyncio
async def test_reauth_wrong_password_is_not_reported_as_session_expiry(
    monkeypatch,
):
    admin = _admin(
        role="super_admin",
        password_hash="hash:super",
        must_change_password=False,
    )
    db = _FakeDB([_Result(admin)])
    monkeypatch.setattr(admin_auth_module, "get_db_ctx", _db_context(db))
    monkeypatch.setattr(
        AdminAuthService,
        "verify_password",
        staticmethod(lambda _password, _password_hash: False),
    )

    with pytest.raises(BusinessException, match="当前密码验证失败") as exc_info:
        await AdminAuthService().reauthenticate(
            admin_id=admin.id,
            expected_auth_version=admin.auth_version,
            jti="browser-session",
            password="incorrect-value",
        )

    assert exc_info.value.code == 40200
    assert exc_info.value.http_status_code == 422
    assert db.commits == 1
    audits = [
        row for row in db.added if getattr(row, "action", None) == "auth.admin_reauth"
    ]
    assert len(audits) == 1
    assert audits[0].result == "failed"
    assert audits[0].reason_code == "current_password_invalid"


@pytest.mark.asyncio
async def test_reauth_stale_auth_version_remains_an_authentication_error(
    monkeypatch,
):
    admin = _admin(
        role="super_admin",
        password_hash="hash:super",
        must_change_password=False,
        auth_version=9,
    )
    db = _FakeDB([_Result(admin)])
    monkeypatch.setattr(admin_auth_module, "get_db_ctx", _db_context(db))

    with pytest.raises(UnauthorizedException) as exc_info:
        await AdminAuthService().reauthenticate(
            admin_id=admin.id,
            expected_auth_version=8,
            jti="stale-browser-session",
            password="irrelevant-value",
        )

    assert exc_info.value.code == 40100
    assert exc_info.value.http_status_code == 401


@pytest.mark.asyncio
async def test_reauth_redis_failure_returns_no_credential_and_is_audited(
    monkeypatch,
):
    admin = _admin(
        role="super_admin",
        password_hash="hash:super",
        must_change_password=False,
    )
    db = _FakeDB([_Result(admin)])
    monkeypatch.setattr(admin_auth_module, "get_db_ctx", _db_context(db))
    monkeypatch.setattr(
        AdminAuthService,
        "verify_password",
        staticmethod(lambda _password, _password_hash: True),
    )
    monkeypatch.setattr(
        admin_auth_module,
        "create_admin_reauth_token",
        AsyncMock(return_value=None),
    )
    service = AdminAuthService()
    service.audit = SimpleNamespace(
        record=AsyncMock(),
        record_best_effort=AsyncMock(),
    )

    with pytest.raises(BusinessException, match="再认证服务暂不可用"):
        await service.reauthenticate(
            admin_id=admin.id,
            expected_auth_version=admin.auth_version,
            jti="browser-session",
            password="current-value",
        )

    service.audit.record.assert_awaited_once()
    service.audit.record_best_effort.assert_awaited_once()


def test_security_audit_append_redacts_sensitive_summary():
    db = _FakeDB()
    row = AdminSecurityAuditService.append(
        db,
        action="admin_account.password_reset",
        result="succeeded",
        actor_admin_id=1,
        target_admin_id=7,
        username=" Quiz.Operator ",
        context=AdminAuditContext(
            request_id="request-1",
            source_ip="192.0.2.8",
            user_agent="test-agent",
        ),
        summary={"password": "must-not-persist", "sessions_invalidated": True},
    )
    assert row.username == "quiz.operator"
    assert row.source_ip == "192.0.2.8"
    assert row.summary == {
        "password": "[REDACTED]",
        "sessions_invalidated": True,
    }
    assert db.added == [row]


@pytest.mark.asyncio
async def test_security_audit_query_keeps_legacy_events_without_id_collisions(
    monkeypatch,
):
    now = datetime.now(timezone.utc)
    legacy_row = {
        "id": -41,
        "action": "admin_account.password_reset",
        "result": "succeeded",
        "reason_code": "legacy_event",
        "actor_admin_id": 1,
        "target_admin_id": 7,
        "username": "quiz.operator",
        "request_id": "request-legacy-41",
        "source_ip": "100.64.0.8",
        "user_agent": "legacy-client Bearer legacy-secret-token",
        "summary": {"password_hash": "must-not-render", "changed": True},
        "created_at": now,
    }

    class _Mappings:
        @staticmethod
        def all():
            return [legacy_row]

    class _Rows:
        @staticmethod
        def mappings():
            return _Mappings()

    class _AuditDB:
        statement = None

        @staticmethod
        async def scalar(_statement):
            return 1

        async def execute(self, statement):
            self.statement = statement
            return _Rows()

    db = _AuditDB()
    monkeypatch.setattr(
        admin_security_audit_module,
        "get_db_ctx",
        _db_context(db),
    )

    page = await AdminSecurityAuditService().list_logs(
        actor_admin_id=None,
        target_admin_id=None,
        action=None,
        result=None,
        username=None,
        request_id=None,
        started_at=None,
        ended_at=None,
        page=1,
        page_size=20,
    )

    assert page.total == 1
    assert page.items[0].id == -41
    assert page.items[0].summary == {
        "password_hash": "[REDACTED]",
        "changed": True,
    }
    assert page.items[0].user_agent == "legacy-client Bearer [REDACTED]"
    compiled = db.statement.compile()
    query = str(compiled)
    assert "UNION ALL" in query
    assert "admin_account.%" in compiled.params.values()
    assert "auth.admin_%" in compiled.params.values()
