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
    command.upgrade(config, "head")

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
        "quiz_import_error",
        "quiz_practice_attempt",
        "quiz_practice_session",
        "quiz_practice_session_question",
        "quiz_question",
        "quiz_question_stats",
        "quiz_user_stats",
        "quiz_wrong_item",
        "quiz_library",
        "quiz_module",
        "quiz_knowledge_point",
        "quiz_question_revision",
        "quiz_question_revision_stats",
        "quiz_course_library_binding",
        "quiz_library_entitlement",
        "quiz_legacy_migration_map",
        "quiz_migration_issue",
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
                    "'uq_quiz_exam_active_user', "
                    "'ix_quiz_practice_attempt_submitted', "
                    "'ix_quiz_exam_status_updated', "
                    "'ix_quiz_exam_answer_updated') ORDER BY indexname"
                )
            ).scalars()
        )
        assert len(index_definitions) == 5
        partial_indexes = [
            definition
            for definition in index_definitions
            if "uq_quiz_" in definition
        ]
        assert len(partial_indexes) == 2
        assert all("WHERE" in definition for definition in partial_indexes)
        assert all("in_progress" in definition for definition in partial_indexes)

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
    command.upgrade(config, "head")
    assert "quiz_record" not in inspect(engine).get_table_names()
    engine.dispose()


def test_quiz_v2_backfills_fixed_hierarchy_versions_and_organization_issues(
    quiz_migration_database,
    monkeypatch,
) -> None:
    config = _alembic_config()
    monkeypatch.setenv(
        "QUIZ_DESTRUCTIVE_MIGRATION_BACKUP_REF",
        "integration-test-verified-backup",
    )
    command.upgrade(config, "quiz003")

    engine = create_engine(quiz_migration_database)
    with engine.begin() as connection:
        admin_id = connection.execute(
            text(
                "INSERT INTO admin_user "
                "(username, password_hash, role, is_active) "
                "VALUES ('quiz-v2-migration-admin', 'test-only', 'super_admin', true) "
                "RETURNING id"
            )
        ).scalar_one()
        root_id = connection.execute(
            text(
                "INSERT INTO quiz_category "
                "(name, normalized_name, parent_id, depth, status, sort_order, "
                "ever_had_question, lock_version, created_by, updated_by) "
                "VALUES ('网络工程师', '网络工程师', NULL, 1, 'active', 1, true, 1, :admin, :admin) "
                "RETURNING id"
            ),
            {"admin": admin_id},
        ).scalar_one()
        module_id = connection.execute(
            text(
                "INSERT INTO quiz_category "
                "(name, normalized_name, parent_id, depth, status, sort_order, "
                "ever_had_question, lock_version, created_by, updated_by) "
                "VALUES ('基础网络', '基础网络', :parent, 2, 'active', 1, true, 1, :admin, :admin) "
                "RETURNING id"
            ),
            {"parent": root_id, "admin": admin_id},
        ).scalar_one()
        point_id = connection.execute(
            text(
                "INSERT INTO quiz_category "
                "(name, normalized_name, parent_id, depth, status, sort_order, "
                "ever_had_question, lock_version, created_by, updated_by) "
                "VALUES ('OSI 模型', 'OSI 模型', :parent, 3, 'active', 1, true, 1, :admin, :admin) "
                "RETURNING id"
            ),
            {"parent": module_id, "admin": admin_id},
        ).scalar_one()

        question_ids: dict[str, int] = {}
        for label, category_id in (
            ("root", root_id),
            ("module", module_id),
            ("point", point_id),
        ):
            question_ids[label] = connection.execute(
                text(
                    "INSERT INTO quiz_question "
                    "(category_id, question_type, status, question_text, "
                    "normalized_question_text, question_text_hash, options, "
                    "correct_answer, explanation, ever_published, published_at, "
                    "disabled_at, lock_version, created_by, updated_by) "
                    "VALUES (:category_id, 'single_choice', 'published', :stem, :stem, "
                    ":hash, CAST('{\"A\": \"一\", \"B\": \"二\", \"C\": \"三\"}' AS jsonb), "
                    "CAST('\"A\"' AS jsonb), '解析', true, now(), NULL, 1, :admin, :admin) "
                    "RETURNING id"
                ),
                {
                    "category_id": category_id,
                    "stem": f"v2-{label}-question",
                    "hash": (label * 64)[:64],
                    "admin": admin_id,
                },
            ).scalar_one()

    command.upgrade(config, "head")

    with engine.connect() as connection:
        library = connection.execute(
            text(
                "SELECT id, access_mode, status, v2_enabled, migration_state "
                "FROM quiz_library WHERE library_code = :code"
            ),
            {"code": f"QL{root_id:014d}"},
        ).mappings().one()
        assert library["access_mode"] == "access_mode_pending"
        assert library["status"] == "draft"
        assert library["v2_enabled"] is False
        assert library["migration_state"] == "needs_organization"

        rows = connection.execute(
            text(
                "SELECT q.id, m.system_kind AS module_kind, "
                "kp.system_kind AS point_kind "
                "FROM quiz_question q "
                "JOIN quiz_knowledge_point kp ON kp.id = q.knowledge_point_id "
                "JOIN quiz_module m ON m.id = kp.module_id "
                "WHERE q.id = ANY(:ids) ORDER BY q.id"
            ),
            {"ids": list(question_ids.values())},
        ).mappings().all()
        by_id = {row["id"]: row for row in rows}
        assert by_id[question_ids["root"]]["module_kind"] == "pending_organization"
        assert by_id[question_ids["root"]]["point_kind"] == "uncategorized"
        assert by_id[question_ids["module"]]["module_kind"] == "none"
        assert by_id[question_ids["module"]]["point_kind"] == "uncategorized"
        assert by_id[question_ids["point"]]["module_kind"] == "none"
        assert by_id[question_ids["point"]]["point_kind"] == "none"

        revision_count = connection.execute(
            text(
                "SELECT count(*) FROM quiz_question_revision "
                "WHERE question_id = ANY(:ids) AND revision_no = 1"
            ),
            {"ids": list(question_ids.values())},
        ).scalar_one()
        mapping_count = connection.execute(
            text(
                "SELECT count(*) FROM quiz_legacy_migration_map "
                "WHERE legacy_object_type = 'question' AND legacy_id = ANY(:ids)"
            ),
            {"ids": list(question_ids.values())},
        ).scalar_one()
        issue_codes = set(
            connection.execute(
                text(
                    "SELECT issue_code FROM quiz_migration_issue "
                    "WHERE library_id = :library_id"
                ),
                {"library_id": library["id"]},
            ).scalars()
        )
        assert revision_count == 3
        assert mapping_count == 3
        assert {
            "question_attached_to_library",
            "question_attached_to_module",
        }.issubset(issue_codes)
    engine.dispose()
