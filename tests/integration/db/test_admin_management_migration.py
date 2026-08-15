"""Isolated PostgreSQL proof for the administrator security migration."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError


pytestmark = pytest.mark.integration_db
REPO_ROOT = Path(__file__).resolve().parents[3]
CREDENTIAL_ACTIONS = (
    "admin_account.create",
    "admin_account.enable",
    "admin_account.password_reset",
)
CREDENTIAL_IDEMPOTENCY_INDEX = (
    "uq_admin_security_audit_credential_idempotency"
)


def _assert_credential_idempotency_schema(inspector) -> None:
    columns = {
        column["name"]: column
        for column in inspector.get_columns("admin_security_audit")
    }
    digest = columns["idempotency_key_hash"]
    assert digest["nullable"] is True
    assert digest["type"].length == 64

    constraints = {
        constraint["name"]: constraint
        for constraint in inspector.get_check_constraints(
            "admin_security_audit"
        )
    }
    length_check = constraints[
        "ck_admin_security_audit_idempotency_hash_length"
    ]["sqltext"]
    assert "idempotency_key_hash IS NULL" in length_check
    assert "length(idempotency_key_hash" in length_check
    assert "= 64" in length_check

    indexes = {
        index["name"]: index
        for index in inspector.get_indexes("admin_security_audit")
    }
    idempotency = indexes[CREDENTIAL_IDEMPOTENCY_INDEX]
    assert idempotency["unique"] is True
    assert idempotency["column_names"] == [
        "actor_admin_id",
        "action",
        "idempotency_key_hash",
    ]
    predicate = str(
        idempotency["dialect_options"]["postgresql_where"]
    )
    assert "result" in predicate and "succeeded" in predicate
    assert "actor_admin_id IS NOT NULL" in predicate
    assert "idempotency_key_hash IS NOT NULL" in predicate
    assert all(action in predicate for action in CREDENTIAL_ACTIONS)


def _insert_audit(
    connection,
    *,
    actor_admin_id: int | None,
    target_admin_id: int,
    action: str,
    result: str,
    digest: str | None,
) -> None:
    connection.execute(
        text(
            "INSERT INTO admin_security_audit "
            "(actor_admin_id, target_admin_id, action, result, "
            "idempotency_key_hash) VALUES "
            "(:actor, :target, :action, :result, :digest)"
        ),
        {
            "actor": actor_admin_id,
            "target": target_admin_id,
            "action": action,
            "result": result,
            "digest": digest,
        },
    )


def _maintenance_url(configured_url: str) -> URL:
    url = make_url(configured_url)
    if not url.drivername.startswith("postgresql"):
        pytest.skip("administrator migration test requires PostgreSQL")
    return url.set(drivername="postgresql+psycopg2", database="postgres")


@pytest.fixture
def admin_migration_database(monkeypatch):
    configured_url = os.getenv("TEST_DATABASE_URL_SYNC")
    if not configured_url:
        pytest.skip("TEST_DATABASE_URL_SYNC is required for real migration tests")

    maintenance_url = _maintenance_url(configured_url)
    database_name = f"admin_security_test_{uuid.uuid4().hex[:12]}"
    maintenance_engine = create_engine(maintenance_url, isolation_level="AUTOCOMMIT")
    try:
        with maintenance_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    except DBAPIError as exc:
        maintenance_engine.dispose()
        pytest.skip(f"PostgreSQL role cannot create an isolated database: {exc}")

    test_url = maintenance_url.set(database=database_name)
    monkeypatch.setenv(
        "TEST_DATABASE_URL_SYNC", test_url.render_as_string(hide_password=False)
    )
    monkeypatch.setenv(
        "TEST_DATABASE_URL",
        test_url.set(drivername="postgresql+asyncpg").render_as_string(
            hide_password=False
        ),
    )
    try:
        yield test_url
    finally:
        with maintenance_engine.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        maintenance_engine.dispose()


def _alembic_config() -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return config


def test_clean_install_upgrade_guards_and_downgrade_rehearsal(
    admin_migration_database, monkeypatch
) -> None:
    config = _alembic_config()
    command.upgrade(config, "head")
    engine = create_engine(admin_migration_database)
    inspector = inspect(engine)
    assert {"admin_password_history", "admin_security_audit"} <= set(
        inspector.get_table_names()
    )
    assert {
        "display_name",
        "must_change_password",
        "auth_version",
        "failed_login_attempts",
        "locked_until",
        "last_login_at",
        "last_login_ip",
        "password_changed_at",
    } <= {column["name"] for column in inspector.get_columns("admin_user")}
    _assert_credential_idempotency_schema(inspector)

    with engine.begin() as connection:
        super_admin_id = connection.execute(
            text(
                "INSERT INTO admin_user (username, password_hash, role, is_active) "
                "VALUES ('  Root.Admin  ', 'hash-one', 'super_admin', true) "
                "RETURNING id"
            )
        ).scalar_one()
        row = connection.execute(
            text(
                "SELECT username, display_name, auth_version, "
                "must_change_password FROM admin_user WHERE id = :id"
            ),
            {"id": super_admin_id},
        ).mappings().one()
        assert dict(row) == {
            "username": "root.admin",
            "display_name": "root.admin",
            "auth_version": 1,
            "must_change_password": True,
        }

    with pytest.raises(DBAPIError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO admin_user "
                    "(username, password_hash, role, is_active) VALUES "
                    "('other.root', 'hash-two', 'super_admin', true)"
                )
            )

    with engine.begin() as connection:
        quiz_admin_id = connection.execute(
            text(
                "INSERT INTO admin_user "
                "(username, display_name, password_hash, role, is_active) VALUES "
                "('quiz.admin', '题库管理员', 'hash-three', 'quiz_admin', true) "
                "RETURNING id"
            )
        ).scalar_one()
        audit_id = connection.execute(
            text(
                "INSERT INTO admin_security_audit "
                "(actor_admin_id, target_admin_id, action, result, username) "
                "VALUES (:actor, :target, 'admin_account.create', 'succeeded', "
                "'quiz.admin') RETURNING id"
            ),
            {"actor": super_admin_id, "target": quiz_admin_id},
        ).scalar_one()

    with pytest.raises(DBAPIError, match="append-only"):
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM admin_security_audit WHERE id = :id"),
                {"id": audit_id},
            )
    with pytest.raises(DBAPIError, match="immutable"):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE admin_user SET username = 'renamed' WHERE id = :id"),
                {"id": quiz_admin_id},
            )

    digest = "a" * 64
    with engine.begin() as connection:
        # The same actor/key may be used once per credential action.  This
        # proves that action is part of the idempotency scope.
        for action in CREDENTIAL_ACTIONS:
            _insert_audit(
                connection,
                actor_admin_id=super_admin_id,
                target_admin_id=quiz_admin_id,
                action=action,
                result="succeeded",
                digest=digest,
            )

    for action in CREDENTIAL_ACTIONS:
        with pytest.raises(DBAPIError, match=CREDENTIAL_IDEMPOTENCY_INDEX):
            with engine.begin() as connection:
                _insert_audit(
                    connection,
                    actor_admin_id=super_admin_id,
                    target_admin_id=quiz_admin_id,
                    action=action,
                    result="succeeded",
                    digest=digest,
                )

    with engine.begin() as connection:
        # Failed attempts and successful non-credential actions remain an
        # append-only history; neither is a completed credential issuance.
        for _ in range(2):
            _insert_audit(
                connection,
                actor_admin_id=super_admin_id,
                target_admin_id=quiz_admin_id,
                action="admin_account.create",
                result="failed",
                digest=digest,
            )
            _insert_audit(
                connection,
                actor_admin_id=super_admin_id,
                target_admin_id=quiz_admin_id,
                action="admin_account.update",
                result="succeeded",
                digest=digest,
            )
        # Missing correlation data is intentionally nullable for legacy,
        # bootstrap, failed-login, and server-command audit events.
        for _ in range(2):
            _insert_audit(
                connection,
                actor_admin_id=super_admin_id,
                target_admin_id=quiz_admin_id,
                action="admin_account.create",
                result="succeeded",
                digest=None,
            )

    with pytest.raises(
        DBAPIError,
        match="ck_admin_security_audit_idempotency_hash_length",
    ):
        with engine.begin() as connection:
            _insert_audit(
                connection,
                actor_admin_id=super_admin_id,
                target_admin_id=quiz_admin_id,
                action="admin_account.create",
                result="failed",
                digest="b" * 63,
            )

    monkeypatch.setenv("ADMIN_SECURITY_DOWNGRADE_BACKUP_REF", "isolated-test-backup")
    command.downgrade(config, "deploy001")
    assert "admin_security_audit" not in inspect(engine).get_table_names()
    with engine.connect() as connection:
        ordinary = connection.execute(
            text("SELECT role, is_active FROM admin_user WHERE id = :id"),
            {"id": quiz_admin_id},
        ).mappings().one()
        assert dict(ordinary) == {"role": "admin", "is_active": True}

    monkeypatch.setenv("ADMIN_SECURITY_MIGRATION_BACKUP_REF", "isolated-test-backup")
    command.upgrade(config, "head")
    _assert_credential_idempotency_schema(inspect(engine))
    with engine.connect() as connection:
        ordinary = connection.execute(
            text(
                "SELECT role, is_active, must_change_password, auth_version "
                "FROM admin_user WHERE id = :id"
            ),
            {"id": quiz_admin_id},
        ).mappings().one()
        assert dict(ordinary) == {
            "role": "quiz_admin",
            "is_active": False,
            "must_change_password": True,
            "auth_version": 2,
        }
    engine.dispose()


def test_existing_install_preflight_rejects_normalized_username_collision(
    admin_migration_database, monkeypatch
) -> None:
    config = _alembic_config()
    command.upgrade(config, "deploy001")
    engine = create_engine(admin_migration_database)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO admin_user (username, password_hash, role, is_active) "
                "VALUES ('root-admin', 'hash', 'super_admin', true), "
                "('Quiz.Operator', 'hash', 'admin', true), "
                "('quiz.operator', 'hash', 'admin', true)"
            )
        )
    monkeypatch.setenv("ADMIN_SECURITY_MIGRATION_BACKUP_REF", "isolated-test-backup")
    with pytest.raises(RuntimeError, match="normalized username conflicts"):
        command.upgrade(config, "head")
    assert "admin_security_audit" not in inspect(engine).get_table_names()
    engine.dispose()


def test_existing_install_requires_one_active_super_admin_and_backup(
    admin_migration_database, monkeypatch
) -> None:
    config = _alembic_config()
    command.upgrade(config, "deploy001")
    engine = create_engine(admin_migration_database)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO admin_user (username, password_hash, role, is_active) "
                "VALUES ('quiz-admin', 'hash', 'admin', true)"
            )
        )
    with pytest.raises(RuntimeError, match="exactly one super_admin"):
        command.upgrade(config, "head")

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO admin_user (username, password_hash, role, is_active) "
                "VALUES ('root-admin', 'hash', 'super_admin', true)"
            )
        )
    with pytest.raises(RuntimeError, match="verify a database backup"):
        command.upgrade(config, "head")
    engine.dispose()
