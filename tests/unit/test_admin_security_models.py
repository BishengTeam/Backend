"""Frozen model and role-policy evidence for ADM-01 through ADM-03."""

from __future__ import annotations

import runpy
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql, sqlite

from app.domain.user.src.index import (
    ADMIN_ROLES,
    AdminPasswordHistory,
    AdminSecurityAudit,
    AdminUser,
)
from app.domain.user.src.model.admin_password_history import (
    _reject_admin_password_history_update,
)
from app.domain.user.src.model.admin_security_audit import (
    ADMIN_CREDENTIAL_IDEMPOTENCY_ACTIONS,
    ADMIN_CREDENTIAL_IDEMPOTENCY_PREDICATE,
    ADMIN_IDEMPOTENCY_DIGEST_LENGTH,
    _reject_admin_security_audit_delete,
    _reject_admin_security_audit_update,
    _sanitize_admin_security_audit,
)
from app.policy.permissions import ROLE_PERMISSIONS
from app.services.admin_security_audit import admin_idempotency_digest


REPO_ROOT = Path(__file__).resolve().parents[2]
QUIZ_ADMIN_PERMISSIONS = {
    "quiz:list",
    "quiz:write",
    "quiz:import",
    "quiz_content_edit",
    "quiz_content_publish",
    "quiz_library_manage",
    "quiz_review",
    "course_quiz_bind",
}


def test_only_frozen_roles_are_persistable_and_quiz_role_is_scoped() -> None:
    assert ADMIN_ROLES == (
        "super_admin",
        "quiz_admin",
        "cert_admin",
        "course_admin",
        "teacher",
    )
    assert set(ROLE_PERMISSIONS) == set(ADMIN_ROLES)
    assert ROLE_PERMISSIONS["super_admin"] == ["*"]
    assert set(ROLE_PERMISSIONS["quiz_admin"]) == QUIZ_ADMIN_PERMISSIONS
    assert not any(
        permission.startswith(("dashboard:", "user:", "order:", "content:", "course:"))
        for permission in ROLE_PERMISSIONS["quiz_admin"]
    )


def test_admin_user_contains_security_state_and_database_guards() -> None:
    columns = AdminUser.__table__.columns
    assert {
        "display_name",
        "must_change_password",
        "auth_version",
        "failed_login_attempts",
        "locked_until",
        "last_login_at",
        "last_login_ip",
        "password_changed_at",
    } <= set(columns.keys())
    assert all(
        not columns[name].nullable
        for name in (
            "display_name",
            "must_change_password",
            "auth_version",
            "failed_login_attempts",
            "password_changed_at",
        )
    )
    assert AdminUser(username="  Quiz.Operator  ").username == "quiz.operator"

    indexes = {index.name: index for index in AdminUser.__table__.indexes}
    assert indexes["uq_admin_user_username_normalized"].unique
    assert indexes["uq_admin_user_single_super_admin"].unique
    where = str(
        indexes["uq_admin_user_single_super_admin"].dialect_options[
            "postgresql"
        ]["where"].compile(dialect=postgresql.dialect())
    )
    assert where == "role = 'super_admin'"


def test_password_history_shape_and_existing_rows_are_immutable() -> None:
    columns = AdminPasswordHistory.__table__.columns
    assert set(columns.keys()) == {"id", "admin_id", "password_hash", "created_at"}
    assert not columns.admin_id.nullable
    assert not columns.password_hash.nullable
    foreign_key = next(iter(columns.admin_id.foreign_keys))
    assert foreign_key.target_fullname == "admin_user.id"
    assert foreign_key.ondelete == "RESTRICT"
    with pytest.raises(ValueError, match="password history is immutable"):
        _reject_admin_password_history_update(None, None, object())


def test_security_audit_is_append_only_and_redacted_at_model_boundary() -> None:
    columns = AdminSecurityAudit.__table__.columns
    assert {
        "actor_admin_id",
        "target_admin_id",
        "action",
        "result",
        "reason_code",
        "username",
        "request_id",
        "idempotency_key_hash",
        "source_ip",
        "user_agent",
        "summary",
        "created_at",
    } <= set(columns.keys())
    row = AdminSecurityAudit(
        action="auth.login.failed",
        result="failed",
        username="  QUIZ.ADMIN  ",
        user_agent=(
            "Bearer top-secret-token; password=plain-value; "
            "reauth_token='second-secret'"
        ),
        summary={"password_hash": "never-store-this", "attempt": 1},
    )
    _sanitize_admin_security_audit(None, None, row)
    assert row.username == "quiz.admin"
    assert row.user_agent == (
        "Bearer [REDACTED]; password=[REDACTED]; "
        "reauth_token=[REDACTED]"
    )
    assert row.summary == {"password_hash": "[REDACTED]", "attempt": 1}
    with pytest.raises(ValueError, match="append-only"):
        _reject_admin_security_audit_update(None, None, row)
    with pytest.raises(ValueError, match="append-only"):
        _reject_admin_security_audit_delete(None, None, row)


def test_credential_idempotency_digest_and_partial_index_are_frozen() -> None:
    table = AdminSecurityAudit.__table__
    digest_column = table.columns.idempotency_key_hash
    assert digest_column.nullable is True
    assert digest_column.type.length == ADMIN_IDEMPOTENCY_DIGEST_LENGTH
    raw_key = "credential-operation-0001"
    persisted_digest = admin_idempotency_digest(raw_key)
    assert len(persisted_digest) == ADMIN_IDEMPOTENCY_DIGEST_LENGTH
    assert set(persisted_digest) <= set("0123456789abcdef")
    assert raw_key not in persisted_digest

    constraints = {constraint.name: constraint for constraint in table.constraints}
    digest_length = str(
        constraints[
            "ck_admin_security_audit_idempotency_hash_length"
        ].sqltext
    )
    assert digest_length == (
        "idempotency_key_hash IS NULL OR length(idempotency_key_hash) = 64"
    )

    indexes = {index.name: index for index in table.indexes}
    idempotency = indexes["uq_admin_security_audit_credential_idempotency"]
    assert idempotency.unique is True
    assert [column.name for column in idempotency.columns] == [
        "actor_admin_id",
        "action",
        "idempotency_key_hash",
    ]
    assert ADMIN_CREDENTIAL_IDEMPOTENCY_ACTIONS == (
        "admin_account.create",
        "admin_account.enable",
        "admin_account.password_reset",
    )
    postgres_predicate = str(
        idempotency.dialect_options["postgresql"]["where"].compile(
            dialect=postgresql.dialect()
        )
    )
    sqlite_predicate = str(
        idempotency.dialect_options["sqlite"]["where"].compile(
            dialect=sqlite.dialect()
        )
    )
    assert postgres_predicate == ADMIN_CREDENTIAL_IDEMPOTENCY_PREDICATE
    assert sqlite_predicate == ADMIN_CREDENTIAL_IDEMPOTENCY_PREDICATE


def test_adm001_migration_declares_preflight_and_clean_install_exception() -> None:
    migration_path = (
        REPO_ROOT / "alembic/versions/adm001_harden_admin_identity_and_roles.py"
    )
    source = migration_path.read_text(encoding="utf-8")
    assert 'revision: str = "adm001"' in source
    assert 'down_revision: str | Sequence[str] | None = "deploy001"' in source
    assert "if not rows:" in source
    assert "exactly one" in source
    assert "normalized username " in source and "conflicts:" in source
    assert "ADMIN_SECURITY_MIGRATION_BACKUP_REF" in source
    assert "uq_admin_user_single_super_admin" in source
    assert "ck_admin_security_audit_idempotency_hash_length" in source
    assert "uq_admin_security_audit_credential_idempotency" in source
    assert "trg_admin_security_audit_append_only" in source

    migration = runpy.run_path(str(migration_path))
    assert (
        migration["ADMIN_IDEMPOTENCY_DIGEST_LENGTH"]
        == ADMIN_IDEMPOTENCY_DIGEST_LENGTH
    )
    assert (
        migration["ADMIN_CREDENTIAL_IDEMPOTENCY_ACTIONS"]
        == ADMIN_CREDENTIAL_IDEMPOTENCY_ACTIONS
    )
    assert (
        migration["ADMIN_CREDENTIAL_IDEMPOTENCY_PREDICATE"]
        == ADMIN_CREDENTIAL_IDEMPOTENCY_PREDICATE
    )
