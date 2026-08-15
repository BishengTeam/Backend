from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.user.src.index import (
    AdminPasswordHistory,
    AdminSecurityAudit,
    AdminUser,
)
from app.port.exceptions import BusinessException, ConflictException, NotFoundException
from app.schemas.admin_settings import AdminSettingsUserCreate
from app.services import admin_settings as service_module
from app.services.admin_security_audit import AdminSecurityAuditService
from app.services.admin_settings import (
    ADMIN_CREDENTIAL_REPLAY_MESSAGE,
    ADMIN_USERNAME_NORMALIZED_CONSTRAINT,
    AdminSettingsService,
)


class _Result:
    def __init__(self, value=None, values=()):
        self.value = value
        self.values = list(values)

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return iter(self.values)


class _FakeDB:
    def __init__(self, results=(), *, scalar_value=None):
        self.results = list(results)
        self.scalar_value = scalar_value
        self.added: list[object] = []
        self.commits = 0
        self.refreshes = 0

    async def execute(self, _statement):
        return self.results.pop(0)

    async def scalar(self, _statement):
        return self.scalar_value

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        for value in self.added:
            if isinstance(value, AdminUser) and value.id is None:
                value.id = 91

    async def commit(self):
        self.commits += 1

    async def refresh(self, value):
        self.refreshes += 1
        now = datetime.now(timezone.utc)
        if getattr(value, "created_at", None) is None:
            value.created_at = now
        if getattr(value, "updated_at", None) is None:
            value.updated_at = now


def _db_context(db: _FakeDB):
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
        "password_changed_at": now,
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _temporary_password_auth(password: str = "GeneratedSecureValue2026"):
    return SimpleNamespace(
        generate_temporary_password=lambda **_kwargs: password,
        hash_password=lambda value: f"hash:{value}",
        _ensure_not_recent_password=AsyncMock(),
        _prune_password_history=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_create_generates_the_temporary_password_and_audits_in_one_commit(
    monkeypatch,
) -> None:
    db = _FakeDB(scalar_value=None)
    monkeypatch.setattr(service_module, "get_db_ctx", _db_context(db))
    service = AdminSettingsService()
    service.auth = _temporary_password_auth()
    service.audit = AdminSecurityAuditService()

    result = await service.create_admin(
        AdminSettingsUserCreate(
            username=" Quiz.New-Operator ",
            display_name=" 新题库运营 ",
        ),
        actor_id=1,
        idempotency_key="create-request-0001",
    )

    assert result.admin.username == "quiz.new-operator"
    assert result.admin.role == "quiz_admin"
    assert result.admin.must_change_password is True
    assert result.temporary_password == "GeneratedSecureValue2026"
    assert db.commits == 1
    assert any(isinstance(row, AdminPasswordHistory) for row in db.added)
    audits = [row for row in db.added if isinstance(row, AdminSecurityAudit)]
    assert len(audits) == 1
    assert audits[0].action == "admin_account.create"
    assert audits[0].result == "succeeded"
    assert audits[0].idempotency_key_hash is not None
    assert audits[0].idempotency_key_hash != "create-request-0001"


@pytest.mark.asyncio
async def test_disable_invalidates_all_sessions_with_the_success_audit(
    monkeypatch,
) -> None:
    admin = _admin()
    db = _FakeDB([_Result(value=admin)])
    monkeypatch.setattr(service_module, "get_db_ctx", _db_context(db))
    service = AdminSettingsService()
    service.audit = AdminSecurityAuditService()

    result = await service.disable_admin(admin.id, actor_id=1)

    assert result.is_active is False
    assert admin.auth_version == 4
    assert db.commits == 1
    audits = [row for row in db.added if isinstance(row, AdminSecurityAudit)]
    assert len(audits) == 1
    assert audits[0].action == "admin_account.disable"
    assert audits[0].summary == {"sessions_invalidated": True}


@pytest.mark.asyncio
async def test_enable_rotates_password_clears_lock_and_keeps_old_sessions_invalid(
    monkeypatch,
) -> None:
    admin = _admin(
        is_active=False,
        failed_login_attempts=5,
        locked_until=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    db = _FakeDB([_Result(value=admin)])
    monkeypatch.setattr(service_module, "get_db_ctx", _db_context(db))
    service = AdminSettingsService()
    service.auth = _temporary_password_auth("EnabledSecureValue2026")
    service.audit = AdminSecurityAuditService()

    result = await service.enable_admin(
        admin.id,
        actor_id=1,
        idempotency_key="enable-request-0001",
    )

    assert result.admin.is_active is True
    assert result.admin.must_change_password is True
    assert result.temporary_password == "EnabledSecureValue2026"
    assert admin.password_hash == "hash:EnabledSecureValue2026"
    assert admin.failed_login_attempts == 0
    assert admin.locked_until is None
    assert admin.auth_version == 4
    assert db.commits == 1
    assert any(isinstance(row, AdminPasswordHistory) for row in db.added)
    assert any(
        isinstance(row, AdminSecurityAudit)
        and row.action == "admin_account.enable"
        for row in db.added
    )


@pytest.mark.asyncio
async def test_unlock_never_reactivates_an_inactive_account(monkeypatch) -> None:
    admin = _admin(
        is_active=False,
        failed_login_attempts=5,
        locked_until=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    db = _FakeDB([_Result(value=admin)])
    monkeypatch.setattr(service_module, "get_db_ctx", _db_context(db))
    service = AdminSettingsService()
    service.audit = AdminSecurityAuditService()

    result = await service.unlock_admin(admin.id, actor_id=1)

    assert result.is_active is False
    assert admin.locked_until is None
    assert admin.failed_login_attempts == 0
    assert admin.auth_version == 3
    assert db.commits == 1


@pytest.mark.asyncio
async def test_password_reset_rejects_an_inactive_account_without_issuing_secret(
    monkeypatch,
) -> None:
    admin = _admin(is_active=False)
    db = _FakeDB([_Result(value=admin)])
    monkeypatch.setattr(service_module, "get_db_ctx", _db_context(db))
    failure_audit = AsyncMock()
    service = AdminSettingsService()
    service.auth = SimpleNamespace(
        generate_temporary_password=lambda **_kwargs: pytest.fail(
            "inactive reset must not generate a credential"
        )
    )
    service.audit = SimpleNamespace(record_best_effort=failure_audit)

    with pytest.raises(BusinessException, match="重新启用"):
        await service.reset_password(
            admin.id,
            actor_id=1,
            idempotency_key="reset-request-0001",
        )

    assert admin.is_active is False
    assert db.commits == 0
    assert db.added == []
    failure_audit.assert_awaited_once()


@pytest.mark.asyncio
async def test_unique_super_admin_is_never_manageable_from_account_routes(
    monkeypatch,
) -> None:
    admin = _admin(role="super_admin")
    db = _FakeDB([_Result(value=admin)])
    monkeypatch.setattr(service_module, "get_db_ctx", _db_context(db))
    failure_audit = AsyncMock()
    service = AdminSettingsService()
    service.audit = SimpleNamespace(
        append=AdminSecurityAuditService.append,
        record_best_effort=failure_audit,
    )

    with pytest.raises(BusinessException, match="唯一超级管理员"):
        await service.disable_admin(admin.id, actor_id=1)

    assert db.commits == 0
    assert admin.is_active is True
    failure_audit.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_account_failure_audit_does_not_reference_a_missing_fk(
    monkeypatch,
) -> None:
    db = _FakeDB([_Result(value=None)])
    monkeypatch.setattr(service_module, "get_db_ctx", _db_context(db))
    failure_audit = AsyncMock()
    service = AdminSettingsService()
    service.audit = SimpleNamespace(record_best_effort=failure_audit)

    with pytest.raises(NotFoundException):
        await service.disable_admin(999_999, actor_id=1)

    failure_audit.assert_awaited_once()
    audit = failure_audit.await_args.kwargs
    assert audit["target_admin_id"] is None
    assert audit["summary"] == {"requested_target_admin_id": 999_999}


@pytest.mark.asyncio
async def test_successful_idempotency_key_is_rejected_before_password_rotation(
    monkeypatch,
) -> None:
    db = _FakeDB(scalar_value=81)
    monkeypatch.setattr(service_module, "get_db_ctx", _db_context(db))
    failure_audit = AsyncMock()
    service = AdminSettingsService()
    service.auth = SimpleNamespace(
        generate_temporary_password=lambda **_kwargs: pytest.fail(
            "a replay must not generate or rotate another credential"
        )
    )
    service.audit = SimpleNamespace(record_best_effort=failure_audit)

    with pytest.raises(ConflictException, match="临时密码不会再次返回") as exc_info:
        await service.reset_password(
            7,
            actor_id=1,
            idempotency_key="reset-request-replayed",
        )

    assert str(exc_info.value.message) == ADMIN_CREDENTIAL_REPLAY_MESSAGE
    assert db.added == []
    assert db.commits == 0
    failure_audit.assert_awaited_once()


def test_integrity_errors_are_only_mapped_for_known_unique_constraints() -> None:
    class _Diagnostic:
        def __init__(self, constraint_name: str) -> None:
            self.constraint_name = constraint_name

    class _OriginalError(Exception):
        def __init__(self, constraint_name: str) -> None:
            self.diag = _Diagnostic(constraint_name)

    username_error = service_module.IntegrityError(
        "insert", {}, _OriginalError(ADMIN_USERNAME_NORMALIZED_CONSTRAINT)
    )
    unrelated_error = service_module.IntegrityError(
        "insert", {}, _OriginalError("fk_unrelated_constraint")
    )

    mapped = AdminSettingsService._integrity_conflict(
        username_error,
        fallback_message="管理员用户名已存在",
        fallback_constraint=ADMIN_USERNAME_NORMALIZED_CONSTRAINT,
    )
    assert isinstance(mapped, ConflictException)
    assert (
        AdminSettingsService._integrity_conflict(
            unrelated_error,
            fallback_message="管理员用户名已存在",
            fallback_constraint=ADMIN_USERNAME_NORMALIZED_CONSTRAINT,
        )
        is None
    )
