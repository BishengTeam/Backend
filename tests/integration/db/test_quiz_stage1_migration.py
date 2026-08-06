"""Real PostgreSQL migration exercise for the frozen quiz foundation.

The test creates and drops its own randomly named database. The configured
PostgreSQL role therefore needs CREATEDB. It never migrates the configured
application or shared test database in place.
"""

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


def _maintenance_url(configured_url: str) -> URL:
    url = make_url(configured_url)
    if not url.drivername.startswith("postgresql"):
        pytest.skip("quiz migration test requires PostgreSQL")
    return url.set(drivername="postgresql+psycopg2", database="postgres")


@pytest.fixture
def quiz_migration_database(monkeypatch):
    configured_url = os.getenv("TEST_DATABASE_URL_SYNC")
    if not configured_url:
        pytest.skip("TEST_DATABASE_URL_SYNC is required for real migration tests")

    maintenance_url = _maintenance_url(configured_url)
    database_name = f"quiz_stage1_test_{uuid.uuid4().hex[:12]}"
    maintenance_engine = create_engine(maintenance_url, isolation_level="AUTOCOMMIT")
    try:
        with maintenance_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    except DBAPIError as exc:
        maintenance_engine.dispose()
        pytest.skip(f"PostgreSQL role cannot create an isolated database: {exc}")

    test_url = maintenance_url.set(database=database_name)
    monkeypatch.setenv(
        "TEST_DATABASE_URL_SYNC",
        test_url.render_as_string(hide_password=False),
    )
    monkeypatch.setenv(
        "TEST_DATABASE_URL",
        test_url.set(drivername="postgresql+asyncpg").render_as_string(
            hide_password=False
        ),
    )
    monkeypatch.delenv("QUIZ_DESTRUCTIVE_MIGRATION_BACKUP_REF", raising=False)
    monkeypatch.delenv("QUIZ_DESTRUCTIVE_DOWNGRADE_BACKUP_REF", raising=False)

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


def test_quiz_rebuild_upgrade_downgrade_upgrade(
    quiz_migration_database,
    monkeypatch,
) -> None:
    config = _alembic_config()
    command.upgrade(config, "rsh001")

    engine = create_engine(quiz_migration_database)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO quiz_category (name, description) "
                "VALUES ('legacy-data', 'forces backup guard')"
            )
        )

    with pytest.raises(RuntimeError, match="quiz tables contain data"):
        command.upgrade(config, "quiz001")

    monkeypatch.setenv(
        "QUIZ_DESTRUCTIVE_MIGRATION_BACKUP_REF",
        "integration-test-verified-backup",
    )
    command.upgrade(config, "quiz001")

    inspector = inspect(engine)
    expected_tables = {
        "quiz_admin_audit_log",
        "quiz_category",
        "quiz_checkin",
        "quiz_collection",
        "quiz_exam",
        "quiz_exam_answer",
        "quiz_exam_question",
        "quiz_import_job",
        "quiz_practice_attempt",
        "quiz_practice_session",
        "quiz_practice_session_question",
        "quiz_question",
        "quiz_question_stats",
        "quiz_user_stats",
        "quiz_wrong_item",
    }
    actual_tables = {
        table for table in inspector.get_table_names() if table.startswith("quiz_")
    }
    assert actual_tables == expected_tables
    assert "quiz_record" not in actual_tables

    with engine.connect() as connection:
        index_definitions = list(
            connection.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE indexname IN "
                    "('uq_quiz_practice_session_active_user', "
                    "'uq_quiz_exam_active_user') ORDER BY indexname"
                )
            ).scalars()
        )
        assert len(index_definitions) == 2
        assert all("WHERE" in definition for definition in index_definitions)
        assert all("in_progress" in definition for definition in index_definitions)

    command.downgrade(config, "rsh001")
    inspector = inspect(engine)
    legacy_tables = {
        table for table in inspector.get_table_names() if table.startswith("quiz_")
    }
    assert legacy_tables == {
        "quiz_category",
        "quiz_checkin",
        "quiz_exam",
        "quiz_question",
        "quiz_record",
    }

    monkeypatch.delenv("QUIZ_DESTRUCTIVE_MIGRATION_BACKUP_REF", raising=False)
    command.upgrade(config, "quiz001")
    assert "quiz_record" not in inspect(engine).get_table_names()
    engine.dispose()
