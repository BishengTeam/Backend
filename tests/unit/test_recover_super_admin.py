from __future__ import annotations

import warnings
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.domain.user.src.index import AdminPasswordHistory, AdminSecurityAudit
from app.port.exceptions import BusinessException
from app.services.admin_auth import AdminAuthService
from scripts import recover_super_admin as recovery_module
from scripts.recover_super_admin import (
    RECOVERY_SWITCH,
    RecoveryRefused,
    SuperAdminIdentity,
    confirmation_phrase,
    recover_super_admin,
    run_interactive,
)


class _Scalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values

    def __iter__(self):
        return iter(self._values)


class _Result:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return _Scalars(self._values)


class _FakeDB(AbstractAsyncContextManager):
    def __init__(self, results, *, fail_flush: bool = False):
        self.results = list(results)
        self.fail_flush = fail_flush
        self.statements = []
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False

    async def execute(self, statement):
        self.statements.append(statement)
        return self.results.pop(0)

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        if self.fail_flush:
            raise RuntimeError("forced flush failure")

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class _SessionFactory:
    def __init__(self, *sessions: _FakeDB):
        self.sessions = list(sessions)

    def __call__(self):
        return self.sessions.pop(0)


class _TTY:
    @staticmethod
    def isatty() -> bool:
        return True


class _NotTTY:
    @staticmethod
    def isatty() -> bool:
        return False


def _admin(**overrides):
    values = {
        "id": 1,
        "username": "root.operator",
        "role": "super_admin",
        "password_hash": "hash:Earlier-Secure-1837",
        "is_active": False,
        "must_change_password": False,
        "auth_version": 7,
        "failed_login_attempts": 5,
        "locked_until": datetime.now(timezone.utc) + timedelta(minutes=10),
        "password_changed_at": datetime.now(timezone.utc),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _patch_fast_passwords(monkeypatch) -> None:
    monkeypatch.setattr(
        AdminAuthService,
        "hash_password",
        staticmethod(lambda password: f"hash:{password}"),
    )
    monkeypatch.setattr(
        AdminAuthService,
        "verify_password",
        staticmethod(lambda password, password_hash: password_hash == f"hash:{password}"),
    )


@pytest.mark.asyncio
async def test_interactive_recovery_rotates_only_unique_super_and_never_prints_password(
    monkeypatch,
):
    _patch_fast_passwords(monkeypatch)
    admin = _admin()
    lookup = _FakeDB([_Result([admin])])
    mutation = _FakeDB([_Result([admin]), _Result([]), _Result([])])
    sessions = _SessionFactory(lookup, mutation)
    password = "Recovery-Secure-4729"
    outputs: list[str] = []
    password_prompts: list[str] = []

    def _read_password(prompt: str) -> str:
        password_prompts.append(prompt)
        return password

    identity = SuperAdminIdentity(id=admin.id, username=admin.username)
    result = await run_interactive(
        session_factory=sessions,
        environ={RECOVERY_SWITCH: "1"},
        stdin=_TTY(),
        input_fn=lambda _prompt: confirmation_phrase(identity),
        password_reader=_read_password,
        print_fn=outputs.append,
    )

    assert result == type(result)(
        admin_id=admin.id,
        username=admin.username,
        auth_version=8,
    )
    assert admin.password_hash == f"hash:{password}"
    assert admin.is_active is True
    assert admin.must_change_password is True
    assert admin.failed_login_attempts == 0
    assert admin.locked_until is None
    assert admin.auth_version == 8
    assert mutation.commits == 1
    assert mutation.rollbacks == 0
    assert len(password_prompts) == 2
    assert password not in "\n".join(outputs + password_prompts)

    histories = [row for row in mutation.added if isinstance(row, AdminPasswordHistory)]
    audits = [row for row in mutation.added if isinstance(row, AdminSecurityAudit)]
    assert len(histories) == 1
    assert histories[0].password_hash == f"hash:{password}"
    assert len(audits) == 1
    assert audits[0].action == "admin_account.emergency_recovery"
    assert audits[0].actor_admin_id is None
    assert audits[0].target_admin_id == admin.id
    assert audits[0].reason_code == "controlled_server_command"
    assert password not in repr(vars(audits[0]))

    lookup_sql = str(lookup.statements[0])
    mutation_sql = str(mutation.statements[0])
    assert "admin_user.role" in lookup_sql
    assert "super_admin" in lookup.statements[0].compile().params.values()
    assert "admin_user.role" in mutation_sql
    assert "super_admin" in mutation.statements[0].compile().params.values()


@pytest.mark.asyncio
async def test_recovery_requires_switch_tty_confirmation_and_exactly_one_super():
    with pytest.raises(RecoveryRefused, match="disabled"):
        await run_interactive(environ={}, stdin=_TTY())

    with pytest.raises(RecoveryRefused, match="interactive server terminal"):
        await run_interactive(
            environ={RECOVERY_SWITCH: "1"},
            stdin=_NotTTY(),
        )

    no_super = _FakeDB([_Result([])])
    with pytest.raises(RecoveryRefused, match="exactly one"):
        await run_interactive(
            session_factory=_SessionFactory(no_super),
            environ={RECOVERY_SWITCH: "1"},
            stdin=_TTY(),
        )

    first = _admin(id=1)
    second = _admin(id=2, username="other.root")
    duplicate_supers = _FakeDB([_Result([first, second])])
    with pytest.raises(RecoveryRefused, match="exactly one"):
        await run_interactive(
            session_factory=_SessionFactory(duplicate_supers),
            environ={RECOVERY_SWITCH: "1"},
            stdin=_TTY(),
        )

    lookup = _FakeDB([_Result([first])])
    password_called = False

    def _unexpected_password_read(_prompt: str) -> str:
        nonlocal password_called
        password_called = True
        return "should-not-be-used"

    with pytest.raises(RecoveryRefused, match="confirmation"):
        await run_interactive(
            session_factory=_SessionFactory(lookup),
            environ={RECOVERY_SWITCH: "1"},
            stdin=_TTY(),
            input_fn=lambda _prompt: "NO",
            password_reader=_unexpected_password_read,
        )
    assert password_called is False


@pytest.mark.asyncio
async def test_recovery_fails_closed_when_no_echo_input_is_unavailable(monkeypatch):
    admin = _admin()
    lookup = _FakeDB([_Result([admin])])
    identity = SuperAdminIdentity(admin.id, admin.username)

    def _insecure_getpass(_prompt: str) -> str:
        warnings.warn("password may be echoed", recovery_module.getpass.GetPassWarning)
        return "must-not-be-read"

    monkeypatch.setattr(recovery_module.getpass, "getpass", _insecure_getpass)
    with pytest.raises(RecoveryRefused, match="no-echo"):
        await run_interactive(
            session_factory=_SessionFactory(lookup),
            environ={RECOVERY_SWITCH: "1"},
            stdin=_TTY(),
            input_fn=lambda _prompt: confirmation_phrase(identity),
        )


def test_cli_rejects_arguments_and_redacts_unexpected_exception_details(
    monkeypatch, capsys
):
    secret_argument = "--password=Must-Never-Appear-4729"
    monkeypatch.setattr(
        recovery_module.sys,
        "argv",
        ["scripts/recover_super_admin.py", secret_argument],
    )
    with pytest.raises(SystemExit):
        recovery_module.main()
    assert secret_argument not in capsys.readouterr().err

    hidden_exception_value = "hash:Must-Never-Appear-5831"
    monkeypatch.setattr(
        recovery_module.sys,
        "argv",
        ["scripts/recover_super_admin.py"],
    )

    def _fail_without_leaking(coroutine):
        coroutine.close()
        raise RuntimeError(hidden_exception_value)

    monkeypatch.setattr(recovery_module.asyncio, "run", _fail_without_leaking)
    with pytest.raises(SystemExit):
        recovery_module.main()
    error_output = capsys.readouterr().err
    assert hidden_exception_value not in error_output
    assert "verify the transaction outcome" in error_output


@pytest.mark.asyncio
async def test_recovery_rejects_recent_password_and_rolls_back(monkeypatch):
    _patch_fast_passwords(monkeypatch)
    admin = _admin(password_hash="hash:Recovery-Secure-4729")
    # The current hash is checked even if a damaged/incomplete history table
    # does not contain it.
    mutation = _FakeDB([_Result([admin])])

    with pytest.raises(BusinessException) as exc_info:
        await recover_super_admin(
            expected_identity=SuperAdminIdentity(admin.id, admin.username),
            new_password="Recovery-Secure-4729",
            session_factory=_SessionFactory(mutation),
        )

    assert exc_info.value.message == "新密码不能与最近 5 次使用的密码相同"
    assert mutation.commits == 0
    assert mutation.rollbacks == 1
    assert mutation.added == []


@pytest.mark.asyncio
async def test_recovery_enforces_frozen_policy_and_cannot_select_another_account():
    admin = _admin()
    weak_password_attempt = _FakeDB([_Result([admin])])
    with pytest.raises(BusinessException) as exc_info:
        await recover_super_admin(
            expected_identity=SuperAdminIdentity(admin.id, admin.username),
            new_password="password1234",
            session_factory=_SessionFactory(weak_password_attempt),
        )
    assert exc_info.value.message == "密码过于常见，请更换更安全的密码"
    assert weak_password_attempt.rollbacks == 1
    assert weak_password_attempt.added == []

    other_account_attempt = _FakeDB([_Result([admin])])
    with pytest.raises(RecoveryRefused, match="identity changed"):
        await recover_super_admin(
            expected_identity=SuperAdminIdentity(99, "quiz.operator"),
            new_password="Recovery-Secure-4729",
            session_factory=_SessionFactory(other_account_attempt),
        )
    assert other_account_attempt.rollbacks == 1
    assert other_account_attempt.added == []


@pytest.mark.asyncio
async def test_recovery_rolls_back_password_history_and_audit_on_transaction_failure(
    monkeypatch,
):
    _patch_fast_passwords(monkeypatch)
    admin = _admin(is_active=True)
    mutation = _FakeDB(
        [_Result([admin]), _Result([])],
        fail_flush=True,
    )

    with pytest.raises(RuntimeError, match="forced flush failure"):
        await recover_super_admin(
            expected_identity=SuperAdminIdentity(admin.id, admin.username),
            new_password="Recovery-Secure-4729",
            session_factory=_SessionFactory(mutation),
        )

    assert mutation.commits == 0
    assert mutation.rollbacks == 1
    assert any(isinstance(row, AdminPasswordHistory) for row in mutation.added)
    assert any(isinstance(row, AdminSecurityAudit) for row in mutation.added)
