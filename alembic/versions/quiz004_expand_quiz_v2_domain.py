"""expand the fixed quiz-library V2 domain without removing legacy data

Revision ID: quiz004
Revises: quiz003
Create Date: 2026-08-13 12:00:00.000000
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "quiz004"
down_revision: str | Sequence[str] | None = "quiz003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

V2_TABLES = (
    "quiz_library",
    "quiz_module",
    "quiz_knowledge_point",
    "quiz_question_revision",
    "quiz_course_library_binding",
    "quiz_library_entitlement",
    "quiz_legacy_migration_map",
    "quiz_migration_issue",
)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def _created_at_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def _create_fixed_hierarchy() -> None:
    op.execute(
        """
        CREATE FUNCTION quiz_library_code() RETURNS text AS $$
          SELECT 'QL' || upper(substr(replace(gen_random_uuid()::text, '-', ''), 1, 20));
        $$ LANGUAGE sql VOLATILE
        """
    )
    op.create_table(
        "quiz_library",
        sa.Column(
            "library_code",
            sa.String(length=32),
            server_default=sa.text("quiz_library_code()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("normalized_name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column("cover_url", sa.String(length=512), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column(
            "access_mode",
            sa.String(length=32),
            server_default="access_mode_pending",
            nullable=False,
        ),
        sa.Column("system_kind", sa.String(length=32), server_default="none", nullable=False),
        sa.Column(
            "migration_state",
            sa.String(length=32),
            server_default="pending_review",
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), server_default="draft", nullable=False),
        sa.Column("v2_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("name_reserved", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("restore_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'suspended', 'archived', 'deleted')",
            name="ck_quiz_library_status",
        ),
        sa.CheckConstraint(
            "access_mode IN ('access_mode_pending', 'free', 'course_entitlement')",
            name="ck_quiz_library_access_mode",
        ),
        sa.CheckConstraint(
            "system_kind IN ('none', 'migration_quarantine')",
            name="ck_quiz_library_system_kind",
        ),
        sa.CheckConstraint(
            "migration_state IN ('pending_review', 'needs_organization', 'ready')",
            name="ck_quiz_library_migration_state",
        ),
        sa.CheckConstraint("lock_version >= 1", name="ck_quiz_library_lock_version"),
        sa.CheckConstraint(
            "((status = 'draft' AND published_at IS NULL AND suspended_at IS NULL "
            "AND archived_at IS NULL AND deleted_at IS NULL) OR "
            "(status = 'published' AND published_at IS NOT NULL "
            "AND suspended_at IS NULL AND archived_at IS NULL AND deleted_at IS NULL) OR "
            "(status = 'suspended' AND published_at IS NOT NULL "
            "AND suspended_at IS NOT NULL AND archived_at IS NULL AND deleted_at IS NULL) OR "
            "(status = 'archived' AND archived_at IS NOT NULL AND deleted_at IS NULL) OR "
            "(status = 'deleted' AND archived_at IS NOT NULL AND deleted_at IS NOT NULL))",
            name="ck_quiz_library_lifecycle",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["admin_user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["admin_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("library_code", name="uq_quiz_library_code"),
    )
    op.create_index(
        "uq_quiz_library_reserved_name",
        "quiz_library",
        ["normalized_name"],
        unique=True,
        postgresql_where=sa.text("name_reserved = true"),
    )
    op.create_index(
        "ix_quiz_library_catalog",
        "quiz_library",
        ["v2_enabled", "status", "sort_order", "id"],
    )
    op.create_index(
        "ix_quiz_library_cleanup",
        "quiz_library",
        ["status", "restore_until", "id"],
    )

    op.create_table(
        "quiz_module",
        sa.Column("library_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("normalized_name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=256), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("system_kind", sa.String(length=32), server_default="none", nullable=False),
        sa.Column("name_reserved", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("restore_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('active', 'disabled', 'deleted')", name="ck_quiz_module_status"
        ),
        sa.CheckConstraint(
            "system_kind IN ('none', 'pending_organization')",
            name="ck_quiz_module_system_kind",
        ),
        sa.CheckConstraint("lock_version >= 1", name="ck_quiz_module_lock_version"),
        sa.CheckConstraint(
            "((status = 'active' AND disabled_at IS NULL AND deleted_at IS NULL) OR "
            "(status = 'disabled' AND disabled_at IS NOT NULL AND deleted_at IS NULL) OR "
            "(status = 'deleted' AND disabled_at IS NOT NULL AND deleted_at IS NOT NULL))",
            name="ck_quiz_module_lifecycle",
        ),
        sa.ForeignKeyConstraint(["library_id"], ["quiz_library.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["admin_user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["admin_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "library_id", name="uq_quiz_module_id_library"),
    )
    op.create_index(
        "uq_quiz_module_reserved_name",
        "quiz_module",
        ["library_id", "normalized_name"],
        unique=True,
        postgresql_where=sa.text("name_reserved = true"),
    )
    op.create_index("ix_quiz_module_tree", "quiz_module", ["library_id", "sort_order", "id"])
    op.create_index("ix_quiz_module_cleanup", "quiz_module", ["status", "restore_until", "id"])

    op.create_table(
        "quiz_knowledge_point",
        sa.Column("library_id", sa.BigInteger(), nullable=False),
        sa.Column("module_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("normalized_name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=256), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("system_kind", sa.String(length=32), server_default="none", nullable=False),
        sa.Column("name_reserved", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("restore_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('active', 'disabled', 'deleted')",
            name="ck_quiz_knowledge_point_status",
        ),
        sa.CheckConstraint(
            "system_kind IN ('none', 'uncategorized')",
            name="ck_quiz_knowledge_point_system_kind",
        ),
        sa.CheckConstraint(
            "lock_version >= 1", name="ck_quiz_knowledge_point_lock_version"
        ),
        sa.CheckConstraint(
            "((status = 'active' AND disabled_at IS NULL AND deleted_at IS NULL) OR "
            "(status = 'disabled' AND disabled_at IS NOT NULL AND deleted_at IS NULL) OR "
            "(status = 'deleted' AND disabled_at IS NOT NULL AND deleted_at IS NOT NULL))",
            name="ck_quiz_knowledge_point_lifecycle",
        ),
        sa.ForeignKeyConstraint(
            ["module_id", "library_id"],
            ["quiz_module.id", "quiz_module.library_id"],
            name="fk_quiz_knowledge_point_module_library",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["admin_user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["admin_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "library_id", name="uq_quiz_knowledge_point_id_library"),
    )
    op.create_index(
        "uq_quiz_knowledge_point_reserved_name",
        "quiz_knowledge_point",
        ["module_id", "normalized_name"],
        unique=True,
        postgresql_where=sa.text("name_reserved = true"),
    )
    op.create_index(
        "ix_quiz_knowledge_point_tree",
        "quiz_knowledge_point",
        ["library_id", "module_id", "sort_order", "id"],
    )
    op.create_index(
        "ix_quiz_knowledge_point_cleanup",
        "quiz_knowledge_point",
        ["status", "restore_until", "id"],
    )


def _expand_question_and_session_tables() -> None:
    op.alter_column(
        "quiz_question",
        "category_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )
    op.add_column("quiz_question", sa.Column("library_id", sa.BigInteger(), nullable=True))
    op.add_column("quiz_question", sa.Column("knowledge_point_id", sa.BigInteger(), nullable=True))
    op.add_column("quiz_question", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("quiz_question", sa.Column("restore_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "quiz_question",
        sa.Column("stem_reserved", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.create_foreign_key(
        "fk_quiz_question_library",
        "quiz_question",
        "quiz_library",
        ["library_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_quiz_question_knowledge_point_library",
        "quiz_question",
        "quiz_knowledge_point",
        ["knowledge_point_id", "library_id"],
        ["id", "library_id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint("ck_quiz_question_lifecycle", "quiz_question", type_="check")
    op.drop_constraint("ck_quiz_question_status", "quiz_question", type_="check")
    op.create_check_constraint(
        "ck_quiz_question_status",
        "quiz_question",
        "status IN ('draft', 'published', 'disabled', 'deleted')",
    )
    op.create_check_constraint(
        "ck_quiz_question_lifecycle",
        "quiz_question",
        "((status = 'draft' AND ever_published = false AND published_at IS NULL "
        "AND disabled_at IS NULL AND deleted_at IS NULL) OR "
        "(status = 'published' AND ever_published = true AND published_at IS NOT NULL "
        "AND deleted_at IS NULL) OR "
        "(status = 'disabled' AND ever_published = true AND published_at IS NOT NULL "
        "AND disabled_at IS NOT NULL AND deleted_at IS NULL) OR "
        "(status = 'deleted' AND deleted_at IS NOT NULL))",
    )
    op.create_check_constraint(
        "ck_quiz_question_v2_assignment",
        "quiz_question",
        "(library_id IS NULL AND knowledge_point_id IS NULL) OR "
        "(library_id IS NOT NULL AND knowledge_point_id IS NOT NULL)",
    )
    op.create_index(
        "ix_quiz_question_v2_pool",
        "quiz_question",
        ["library_id", "knowledge_point_id", "status", "id"],
    )

    op.create_table(
        "quiz_question_revision",
        sa.Column("question_id", sa.BigInteger(), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("question_type", sa.String(length=24), nullable=False),
        sa.Column("question_text", sa.String(length=1024), nullable=False),
        sa.Column("normalized_question_text", sa.String(length=1024), nullable=False),
        sa.Column("question_text_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("correct_answer", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("explanation", sa.String(length=1024), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        *_created_at_columns(),
        sa.CheckConstraint(
            "question_type IN ('single_choice', 'multiple_choice', 'judge')",
            name="ck_quiz_question_revision_type",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'superseded', 'discarded')",
            name="ck_quiz_question_revision_status",
        ),
        sa.CheckConstraint("revision_no >= 1", name="ck_quiz_question_revision_no"),
        sa.CheckConstraint(
            "((status = 'published' AND published_at IS NOT NULL) OR status <> 'published')",
            name="ck_quiz_question_revision_lifecycle",
        ),
        sa.ForeignKeyConstraint(["question_id"], ["quiz_question.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["admin_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "question_id", "revision_no", name="uq_quiz_question_revision_number"
        ),
        sa.UniqueConstraint(
            "id", "question_id", name="uq_quiz_question_revision_id_question"
        ),
    )
    op.create_index(
        "uq_quiz_question_pending_revision",
        "quiz_question_revision",
        ["question_id"],
        unique=True,
        postgresql_where=sa.text("status = 'draft'"),
    )
    op.create_index(
        "ix_quiz_question_revision_question",
        "quiz_question_revision",
        ["question_id", "revision_no"],
    )
    op.add_column("quiz_question", sa.Column("current_revision_id", sa.BigInteger(), nullable=True))
    op.add_column("quiz_question", sa.Column("pending_revision_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_quiz_question_current_revision",
        "quiz_question",
        "quiz_question_revision",
        ["current_revision_id", "id"],
        ["id", "question_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_quiz_question_pending_revision",
        "quiz_question",
        "quiz_question_revision",
        ["pending_revision_id", "id"],
        ["id", "question_id"],
        ondelete="RESTRICT",
    )

    op.drop_index("uq_quiz_practice_session_active_user", table_name="quiz_practice_session")
    op.drop_constraint("ck_quiz_practice_session_mode", "quiz_practice_session", type_="check")
    op.drop_constraint("ck_quiz_practice_session_status", "quiz_practice_session", type_="check")
    op.drop_constraint("ck_quiz_practice_session_request", "quiz_practice_session", type_="check")
    op.drop_constraint("ck_quiz_practice_session_actual_count", "quiz_practice_session", type_="check")
    op.drop_constraint("ck_quiz_practice_session_lifecycle", "quiz_practice_session", type_="check")
    op.add_column("quiz_practice_session", sa.Column("library_id", sa.BigInteger(), nullable=True))
    op.add_column("quiz_practice_session", sa.Column("scope_type", sa.String(length=24), nullable=True))
    op.add_column("quiz_practice_session", sa.Column("scope_id", sa.BigInteger(), nullable=True))
    op.add_column("quiz_practice_session", sa.Column("last_answered_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("quiz_practice_session", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("quiz_practice_session", sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("quiz_practice_session", sa.Column("pause_reason", sa.String(length=64), nullable=True))
    op.add_column(
        "quiz_practice_session",
        sa.Column("paused_seconds", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("quiz_practice_session", sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("quiz_practice_session", sa.Column("terminated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("quiz_practice_session", sa.Column("termination_reason", sa.String(length=64), nullable=True))
    op.create_foreign_key(
        "fk_quiz_practice_session_library",
        "quiz_practice_session",
        "quiz_library",
        ["library_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_quiz_practice_session_mode",
        "quiz_practice_session",
        "mode IN ('normal', 'wrong', 'full', 'wrong_only', 'legacy_limited')",
    )
    op.create_check_constraint(
        "ck_quiz_practice_session_status",
        "quiz_practice_session",
        "status IN ('in_progress', 'paused', 'completed', 'abandoned', 'expired', 'terminated')",
    )
    op.create_check_constraint(
        "ck_quiz_practice_session_request",
        "quiz_practice_session",
        "((mode = 'normal' AND category_id IS NOT NULL AND requested_count BETWEEN 10 AND 100) OR "
        "(mode = 'wrong' AND category_id IS NULL AND requested_count = 20) OR "
        "(mode = 'legacy_limited' AND requested_count BETWEEN 1 AND 100) OR "
        "(mode IN ('full', 'wrong_only') AND scope_type IS NOT NULL "
        "AND scope_id IS NOT NULL AND requested_count >= 1))",
    )
    op.create_check_constraint(
        "ck_quiz_practice_session_actual_count", "quiz_practice_session", "actual_count >= 1"
    )
    op.create_check_constraint(
        "ck_quiz_practice_session_lifecycle",
        "quiz_practice_session",
        "((status IN ('in_progress', 'paused') AND completed_at IS NULL "
        "AND abandoned_at IS NULL AND terminated_at IS NULL) OR "
        "(status = 'completed' AND completed_at IS NOT NULL AND abandoned_at IS NULL) OR "
        "(status = 'abandoned' AND completed_at IS NULL AND abandoned_at IS NOT NULL) OR "
        "(status = 'expired' AND expired_at IS NOT NULL) OR "
        "(status = 'terminated' AND terminated_at IS NOT NULL))",
    )
    op.create_check_constraint(
        "ck_quiz_practice_session_scope_type",
        "quiz_practice_session",
        "scope_type IS NULL OR scope_type IN ('library', 'module', 'knowledge_point')",
    )
    op.create_check_constraint(
        "ck_quiz_practice_session_paused_seconds",
        "quiz_practice_session",
        "paused_seconds >= 0",
    )
    op.create_index(
        "uq_quiz_practice_session_active_user",
        "quiz_practice_session",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'in_progress' AND scope_type IS NULL"),
    )
    op.create_index(
        "uq_quiz_practice_session_active_scope",
        "quiz_practice_session",
        ["user_id", "scope_type", "scope_id", "mode"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('in_progress', 'paused') AND scope_type IS NOT NULL"
        ),
    )

    op.add_column(
        "quiz_practice_session_question",
        sa.Column("question_revision_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "quiz_practice_session_question",
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "quiz_practice_session_question",
        sa.Column("skip_count", sa.SmallInteger(), server_default="0", nullable=False),
    )
    op.create_foreign_key(
        "fk_quiz_practice_question_revision",
        "quiz_practice_session_question",
        "quiz_question_revision",
        ["question_revision_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_quiz_practice_question_skip_count",
        "quiz_practice_session_question",
        "skip_count BETWEEN 0 AND 1",
    )

    op.add_column("quiz_import_job", sa.Column("library_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_quiz_import_job_library",
        "quiz_import_job",
        "quiz_library",
        ["library_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def _create_entitlement_and_migration_tables() -> None:
    op.create_table(
        "quiz_course_library_binding",
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("library_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('active', 'inactive')",
            name="ck_quiz_course_library_binding_status",
        ),
        sa.CheckConstraint(
            "lock_version >= 1", name="ck_quiz_course_library_binding_lock_version"
        ),
        sa.ForeignKeyConstraint(["course_id"], ["course.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["library_id"], ["quiz_library.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["admin_user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["admin_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("course_id", "library_id", name="uq_quiz_course_library_binding"),
    )
    op.create_index(
        "ix_quiz_course_library_binding_active",
        "quiz_course_library_binding",
        ["course_id", "status", "library_id"],
    )

    op.create_table(
        "quiz_library_entitlement",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("library_id", sa.BigInteger(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=24), server_default="course_order", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('active', 'revoked', 'expired')",
            name="ck_quiz_library_entitlement_status",
        ),
        sa.CheckConstraint(
            "source_type = 'course_order'",
            name="ck_quiz_library_entitlement_source_type",
        ),
        sa.CheckConstraint(
            "ends_at IS NULL OR ends_at > starts_at",
            name="ck_quiz_library_entitlement_period",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["library_id"], ["quiz_library.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["course_id"], ["course.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["order_id"], ["order.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "order_id", "library_id", name="uq_quiz_library_entitlement_order_library"
        ),
    )
    op.create_index(
        "ix_quiz_library_entitlement_access",
        "quiz_library_entitlement",
        ["user_id", "library_id", "status", "starts_at", "ends_at"],
    )

    op.create_table(
        "quiz_legacy_migration_map",
        sa.Column("legacy_object_type", sa.String(length=24), nullable=False),
        sa.Column("legacy_id", sa.BigInteger(), nullable=False),
        sa.Column("target_object_type", sa.String(length=24), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("library_id", sa.BigInteger(), nullable=False),
        sa.Column("migration_status", sa.String(length=24), nullable=False),
        sa.Column("original_path", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("issue_code", sa.String(length=64), nullable=True),
        *_created_at_columns(),
        sa.CheckConstraint(
            "legacy_object_type IN ('category', 'question')",
            name="ck_quiz_legacy_migration_object_type",
        ),
        sa.CheckConstraint(
            "target_object_type IN ('library', 'module', 'knowledge_point', 'question')",
            name="ck_quiz_legacy_migration_target_type",
        ),
        sa.CheckConstraint(
            "migration_status IN ('mapped', 'quarantined')",
            name="ck_quiz_legacy_migration_status",
        ),
        sa.ForeignKeyConstraint(["library_id"], ["quiz_library.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "legacy_object_type", "legacy_id", name="uq_quiz_legacy_migration_source"
        ),
    )
    op.create_index(
        "ix_quiz_legacy_migration_target",
        "quiz_legacy_migration_map",
        ["target_object_type", "target_id"],
    )

    op.create_table(
        "quiz_migration_issue",
        sa.Column("library_id", sa.BigInteger(), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="open", nullable=False),
        sa.Column("issue_code", sa.String(length=64), nullable=False),
        sa.Column("legacy_object_type", sa.String(length=24), nullable=False),
        sa.Column("legacy_id", sa.BigInteger(), nullable=False),
        sa.Column("original_path", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("resolution", sa.String(length=256), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        *_created_at_columns(),
        sa.CheckConstraint(
            "severity IN ('warning', 'blocking')", name="ck_quiz_migration_issue_severity"
        ),
        sa.CheckConstraint(
            "legacy_object_type IN ('category', 'question')",
            name="ck_quiz_migration_issue_object_type",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'resolved')", name="ck_quiz_migration_issue_status"
        ),
        sa.ForeignKeyConstraint(["library_id"], ["quiz_library.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "issue_code",
            "legacy_object_type",
            "legacy_id",
            name="uq_quiz_migration_issue_source",
        ),
    )
    op.create_index(
        "ix_quiz_migration_issue_library",
        "quiz_migration_issue",
        ["library_id", "status", "id"],
    )


def _install_immutability_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION quiz_library_code_immutable() RETURNS trigger AS $$
        BEGIN
          IF NEW.library_code IS DISTINCT FROM OLD.library_code THEN
            RAISE EXCEPTION 'quiz library code is immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_quiz_library_code_immutable
        BEFORE UPDATE ON quiz_library
        FOR EACH ROW EXECUTE FUNCTION quiz_library_code_immutable()
        """
    )
    op.execute(
        """
        CREATE FUNCTION quiz_question_revision_content_immutable() RETURNS trigger AS $$
        BEGIN
          IF NEW.question_id IS DISTINCT FROM OLD.question_id
             OR NEW.revision_no IS DISTINCT FROM OLD.revision_no
             OR NEW.question_type IS DISTINCT FROM OLD.question_type
             OR NEW.question_text IS DISTINCT FROM OLD.question_text
             OR NEW.normalized_question_text IS DISTINCT FROM OLD.normalized_question_text
             OR NEW.question_text_hash IS DISTINCT FROM OLD.question_text_hash
             OR NEW.options IS DISTINCT FROM OLD.options
             OR NEW.correct_answer IS DISTINCT FROM OLD.correct_answer
             OR NEW.explanation IS DISTINCT FROM OLD.explanation
             OR NEW.created_by IS DISTINCT FROM OLD.created_by
             OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'quiz question revision content is immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_quiz_question_revision_content_immutable
        BEFORE UPDATE ON quiz_question_revision
        FOR EACH ROW EXECUTE FUNCTION quiz_question_revision_content_immutable()
        """
    )


def _backfill_legacy_tree() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            INSERT INTO quiz_library (
              library_code, name, normalized_name, description,
              access_mode, system_kind, migration_state, status, v2_enabled,
              name_reserved, sort_order, lock_version, created_by, updated_by,
              created_at, updated_at
            )
            SELECT
              'QL' || lpad(c.id::text, 14, '0'), c.name, c.normalized_name,
              c.description, 'access_mode_pending', 'none', 'pending_review',
              'draft', false, true, c.sort_order, 1, c.created_by, c.updated_by,
              c.created_at, c.updated_at
            FROM quiz_category c
            WHERE c.depth = 1 AND c.parent_id IS NULL
            ORDER BY c.id
            ON CONFLICT (library_code) DO NOTHING
            """
        )
    )
    bind.execute(
        sa.text(
            """
            INSERT INTO quiz_legacy_migration_map (
              legacy_object_type, legacy_id, target_object_type, target_id,
              library_id, migration_status, original_path
            )
            SELECT 'category', c.id, 'library', l.id, l.id, 'mapped',
                   jsonb_build_array(jsonb_build_object('id', c.id, 'name', c.name))
            FROM quiz_category c
            JOIN quiz_library l ON l.library_code = 'QL' || lpad(c.id::text, 14, '0')
            WHERE c.depth = 1 AND c.parent_id IS NULL
            ON CONFLICT (legacy_object_type, legacy_id) DO NOTHING
            """
        )
    )
    bind.execute(
        sa.text(
            """
            INSERT INTO quiz_module (
              library_id, name, normalized_name, description, status, system_kind,
              name_reserved, sort_order, lock_version, disabled_at,
              created_by, updated_by, created_at, updated_at
            )
            SELECT l.id, c.name, c.normalized_name, c.description, c.status, 'none',
                   true, c.sort_order, c.lock_version,
                   CASE WHEN c.status = 'disabled' THEN c.updated_at ELSE NULL END,
                   c.created_by, c.updated_by, c.created_at, c.updated_at
            FROM quiz_category c
            JOIN quiz_category root ON root.id = c.parent_id
            JOIN quiz_library l ON l.library_code = 'QL' || lpad(root.id::text, 14, '0')
            WHERE c.depth = 2 AND root.depth = 1 AND root.parent_id IS NULL
            ORDER BY c.id
            """
        )
    )
    bind.execute(
        sa.text(
            """
            INSERT INTO quiz_legacy_migration_map (
              legacy_object_type, legacy_id, target_object_type, target_id,
              library_id, migration_status, original_path
            )
            SELECT 'category', c.id, 'module', m.id, l.id, 'mapped',
                   jsonb_build_array(
                     jsonb_build_object('id', root.id, 'name', root.name),
                     jsonb_build_object('id', c.id, 'name', c.name)
                   )
            FROM quiz_category c
            JOIN quiz_category root ON root.id = c.parent_id
            JOIN quiz_library l ON l.library_code = 'QL' || lpad(root.id::text, 14, '0')
            JOIN quiz_module m
              ON m.library_id = l.id AND m.normalized_name = c.normalized_name
            WHERE c.depth = 2 AND root.depth = 1 AND root.parent_id IS NULL
            ON CONFLICT (legacy_object_type, legacy_id) DO NOTHING
            """
        )
    )
    bind.execute(
        sa.text(
            """
            INSERT INTO quiz_knowledge_point (
              library_id, module_id, name, normalized_name, description, status,
              system_kind, name_reserved, sort_order, lock_version, disabled_at,
              created_by, updated_by, created_at, updated_at
            )
            SELECT l.id, m.id, c.name, c.normalized_name, c.description, c.status,
                   'none', true, c.sort_order, c.lock_version,
                   CASE WHEN c.status = 'disabled' THEN c.updated_at ELSE NULL END,
                   c.created_by, c.updated_by, c.created_at, c.updated_at
            FROM quiz_category c
            JOIN quiz_category parent ON parent.id = c.parent_id
            JOIN quiz_category root ON root.id = parent.parent_id
            JOIN quiz_library l ON l.library_code = 'QL' || lpad(root.id::text, 14, '0')
            JOIN quiz_module m
              ON m.library_id = l.id AND m.normalized_name = parent.normalized_name
            WHERE c.depth = 3 AND parent.depth = 2
              AND root.depth = 1 AND root.parent_id IS NULL
            ORDER BY c.id
            """
        )
    )
    bind.execute(
        sa.text(
            """
            INSERT INTO quiz_legacy_migration_map (
              legacy_object_type, legacy_id, target_object_type, target_id,
              library_id, migration_status, original_path
            )
            SELECT 'category', c.id, 'knowledge_point', kp.id, l.id, 'mapped',
                   jsonb_build_array(
                     jsonb_build_object('id', root.id, 'name', root.name),
                     jsonb_build_object('id', parent.id, 'name', parent.name),
                     jsonb_build_object('id', c.id, 'name', c.name)
                   )
            FROM quiz_category c
            JOIN quiz_category parent ON parent.id = c.parent_id
            JOIN quiz_category root ON root.id = parent.parent_id
            JOIN quiz_library l ON l.library_code = 'QL' || lpad(root.id::text, 14, '0')
            JOIN quiz_module m
              ON m.library_id = l.id AND m.normalized_name = parent.normalized_name
            JOIN quiz_knowledge_point kp
              ON kp.module_id = m.id AND kp.normalized_name = c.normalized_name
            WHERE c.depth = 3 AND parent.depth = 2
              AND root.depth = 1 AND root.parent_id IS NULL
            ON CONFLICT (legacy_object_type, legacy_id) DO NOTHING
            """
        )
    )

    # Root-level questions are migrated into an internal pending subtree.
    bind.execute(
        sa.text(
            """
            INSERT INTO quiz_module (
              library_id, name, normalized_name, status, system_kind,
              name_reserved, sort_order, lock_version, created_by, updated_by
            )
            SELECT DISTINCT l.id, '待整理', '__system_pending_organization__',
                   'active', 'pending_organization', true, 2147483646, 1,
                   c.created_by, c.updated_by
            FROM quiz_question q
            JOIN quiz_category c ON c.id = q.category_id
            JOIN quiz_library l ON l.library_code = 'QL' || lpad(c.id::text, 14, '0')
            WHERE c.depth = 1 AND c.parent_id IS NULL
            ON CONFLICT DO NOTHING
            """
        )
    )
    bind.execute(
        sa.text(
            """
            INSERT INTO quiz_knowledge_point (
              library_id, module_id, name, normalized_name, status, system_kind,
              name_reserved, sort_order, lock_version, created_by, updated_by
            )
            SELECT m.library_id, m.id, '未分类', '__system_uncategorized__',
                   'active', 'uncategorized', true, 2147483646, 1,
                   m.created_by, m.updated_by
            FROM quiz_module m
            WHERE m.system_kind = 'pending_organization'
            ON CONFLICT DO NOTHING
            """
        )
    )
    # Module-level questions get an internal uncategorized knowledge point.
    bind.execute(
        sa.text(
            """
            INSERT INTO quiz_knowledge_point (
              library_id, module_id, name, normalized_name, status, system_kind,
              name_reserved, sort_order, lock_version, created_by, updated_by
            )
            SELECT DISTINCT m.library_id, m.id, '未分类', '__system_uncategorized__',
                   'active', 'uncategorized', true, 2147483646, 1,
                   c.created_by, c.updated_by
            FROM quiz_question q
            JOIN quiz_category c ON c.id = q.category_id
            JOIN quiz_legacy_migration_map mm
              ON mm.legacy_object_type = 'category' AND mm.legacy_id = c.id
             AND mm.target_object_type = 'module'
            JOIN quiz_module m ON m.id = mm.target_id
            WHERE c.depth = 2
            ON CONFLICT DO NOTHING
            """
        )
    )

    # First map all valid depth-three questions.
    bind.execute(
        sa.text(
            """
            UPDATE quiz_question q
            SET library_id = mm.library_id, knowledge_point_id = mm.target_id
            FROM quiz_legacy_migration_map mm
            WHERE mm.legacy_object_type = 'category'
              AND mm.target_object_type = 'knowledge_point'
              AND mm.legacy_id = q.category_id
            """
        )
    )
    # Then map questions directly attached to modules.
    bind.execute(
        sa.text(
            """
            UPDATE quiz_question q
            SET library_id = mm.library_id, knowledge_point_id = kp.id
            FROM quiz_legacy_migration_map mm
            JOIN quiz_knowledge_point kp
              ON kp.module_id = mm.target_id AND kp.system_kind = 'uncategorized'
            WHERE mm.legacy_object_type = 'category'
              AND mm.target_object_type = 'module'
              AND mm.legacy_id = q.category_id
              AND q.library_id IS NULL
            """
        )
    )
    # Finally map questions directly attached to root categories.
    bind.execute(
        sa.text(
            """
            UPDATE quiz_question q
            SET library_id = mm.library_id, knowledge_point_id = kp.id
            FROM quiz_legacy_migration_map mm
            JOIN quiz_module m
              ON m.library_id = mm.library_id AND m.system_kind = 'pending_organization'
            JOIN quiz_knowledge_point kp
              ON kp.module_id = m.id AND kp.system_kind = 'uncategorized'
            WHERE mm.legacy_object_type = 'category'
              AND mm.target_object_type = 'library'
              AND mm.legacy_id = q.category_id
              AND q.library_id IS NULL
            """
        )
    )

    # Quarantine structurally invalid leftovers instead of guessing ownership.
    # Keep this branch entirely SQL-driven so Alembic can also render a
    # faithful offline migration without trying to read a result from its mock
    # connection.  The following statements naturally become no-ops when the
    # conditional library row is not created.
    def _quarantine_unmapped() -> None:
        bind.execute(
            sa.text(
                """
                INSERT INTO quiz_library (
                  library_code, name, normalized_name, description, access_mode,
                  system_kind, migration_state, status, v2_enabled, name_reserved,
                  sort_order, lock_version
                )
                SELECT
                  'QLSYSTEMQUARANTINE', '迁移隔离区', '__system_migration_quarantine__',
                  '旧分类结构异常的隔离区', 'access_mode_pending',
                  'migration_quarantine', 'needs_organization', 'draft', false,
                  true, 2147483647, 1
                WHERE EXISTS (SELECT 1 FROM quiz_question WHERE library_id IS NULL)
                   OR EXISTS (
                     SELECT 1 FROM quiz_category c
                     WHERE NOT EXISTS (
                       SELECT 1 FROM quiz_legacy_migration_map mm
                       WHERE mm.legacy_object_type = 'category'
                         AND mm.legacy_id = c.id
                     )
                   )
                ON CONFLICT (library_code) DO NOTHING
                """
            )
        )
        bind.execute(
            sa.text(
                """
                INSERT INTO quiz_module (
                  library_id, name, normalized_name, status, system_kind,
                  name_reserved, sort_order, lock_version
                )
                SELECT id, '待整理', '__system_pending_organization__', 'active',
                       'pending_organization', true, 2147483646, 1
                FROM quiz_library WHERE library_code = 'QLSYSTEMQUARANTINE'
                ON CONFLICT DO NOTHING
                """
            )
        )
        bind.execute(
            sa.text(
                """
                INSERT INTO quiz_knowledge_point (
                  library_id, module_id, name, normalized_name, status, system_kind,
                  name_reserved, sort_order, lock_version
                )
                SELECT m.library_id, m.id, '未分类', '__system_uncategorized__',
                       'active', 'uncategorized', true, 2147483646, 1
                FROM quiz_module m
                JOIN quiz_library l ON l.id = m.library_id
                WHERE l.library_code = 'QLSYSTEMQUARANTINE'
                ON CONFLICT DO NOTHING
                """
            )
        )
        bind.execute(
            sa.text(
                """
                INSERT INTO quiz_legacy_migration_map (
                  legacy_object_type, legacy_id, target_object_type, target_id,
                  library_id, migration_status, original_path, issue_code
                )
                SELECT 'category', c.id, 'module', m.id, l.id, 'quarantined',
                       jsonb_build_array(jsonb_build_object('id', c.id, 'name', c.name)),
                       'invalid_legacy_hierarchy'
                FROM quiz_category c
                JOIN quiz_library l ON l.library_code = 'QLSYSTEMQUARANTINE'
                JOIN quiz_module m
                  ON m.library_id = l.id AND m.system_kind = 'pending_organization'
                WHERE NOT EXISTS (
                  SELECT 1 FROM quiz_legacy_migration_map mm
                  WHERE mm.legacy_object_type = 'category' AND mm.legacy_id = c.id
                )
                ON CONFLICT (legacy_object_type, legacy_id) DO NOTHING
                """
            )
        )
        bind.execute(
            sa.text(
                """
                INSERT INTO quiz_migration_issue (
                  library_id, severity, status, issue_code, legacy_object_type,
                  legacy_id, original_path, resolution
                )
                SELECT mm.library_id, 'blocking', 'open', mm.issue_code,
                       'category', mm.legacy_id, mm.original_path,
                       '分类结构无有效固定层级映射，已保留在迁移隔离区'
                FROM quiz_legacy_migration_map mm
                WHERE mm.legacy_object_type = 'category'
                  AND mm.migration_status = 'quarantined'
                ON CONFLICT (issue_code, legacy_object_type, legacy_id) DO NOTHING
                """
            )
        )
        bind.execute(
            sa.text(
                """
                UPDATE quiz_question q
                SET library_id = l.id, knowledge_point_id = kp.id
                FROM quiz_library l
                JOIN quiz_module m ON m.library_id = l.id
                JOIN quiz_knowledge_point kp ON kp.module_id = m.id
                WHERE l.library_code = 'QLSYSTEMQUARANTINE'
                  AND m.system_kind = 'pending_organization'
                  AND kp.system_kind = 'uncategorized'
                  AND q.library_id IS NULL
                """
            )
        )

    _quarantine_unmapped()

    bind.execute(
        sa.text(
            """
            INSERT INTO quiz_question_revision (
              question_id, revision_no, status, question_type, question_text,
              normalized_question_text, question_text_hash, options,
              correct_answer, explanation, published_at, created_by, created_at
            )
            SELECT q.id, 1,
                   CASE WHEN q.ever_published THEN 'published' ELSE 'draft' END,
                   q.question_type, q.question_text, q.normalized_question_text,
                   q.question_text_hash, q.options, q.correct_answer, q.explanation,
                   CASE WHEN q.ever_published THEN q.published_at ELSE NULL END,
                   q.created_by, q.created_at
            FROM quiz_question q
            ORDER BY q.id
            ON CONFLICT (question_id, revision_no) DO NOTHING
            """
        )
    )
    # Existing recursive categories allowed the same stem in different leaves.
    # Keep every row for review, release the reservation for duplicate groups,
    # and report them before installing the V2 library-wide unique index.
    bind.execute(
        sa.text(
            """
            UPDATE quiz_question q
            SET stem_reserved = false
            WHERE (q.library_id, q.question_text_hash) IN (
              SELECT library_id, question_text_hash
              FROM quiz_question
              WHERE library_id IS NOT NULL
              GROUP BY library_id, question_text_hash
              HAVING count(*) > 1
            )
            """
        )
    )
    op.create_index(
        "uq_quiz_question_library_text_hash",
        "quiz_question",
        ["library_id", "question_text_hash"],
        unique=True,
        postgresql_where=sa.text("library_id IS NOT NULL AND stem_reserved = true"),
    )
    bind.execute(
        sa.text(
            """
            UPDATE quiz_question q
            SET current_revision_id = CASE WHEN q.ever_published THEN r.id ELSE NULL END,
                pending_revision_id = CASE WHEN q.ever_published THEN NULL ELSE r.id END
            FROM quiz_question_revision r
            WHERE r.question_id = q.id AND r.revision_no = 1
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE quiz_practice_session_question s
            SET question_revision_id = q.current_revision_id
            FROM quiz_question q
            WHERE q.id = s.question_id AND q.current_revision_id IS NOT NULL
            """
        )
    )

    bind.execute(
        sa.text(
            """
            INSERT INTO quiz_legacy_migration_map (
              legacy_object_type, legacy_id, target_object_type, target_id,
              library_id, migration_status, original_path, issue_code
            )
            SELECT 'question', q.id, 'question', q.id, q.library_id,
                   CASE WHEN c.depth = 3 AND cm.target_object_type = 'knowledge_point'
                        THEN 'mapped' ELSE 'quarantined' END,
                   COALESCE(cm.original_path,
                     jsonb_build_array(jsonb_build_object('id', c.id, 'name', c.name))),
                   CASE
                     WHEN c.depth = 1 THEN 'question_attached_to_library'
                     WHEN c.depth = 2 THEN 'question_attached_to_module'
                     WHEN c.depth = 3 AND cm.target_object_type = 'knowledge_point' THEN NULL
                     ELSE 'invalid_legacy_hierarchy'
                   END
            FROM quiz_question q
            JOIN quiz_category c ON c.id = q.category_id
            LEFT JOIN quiz_legacy_migration_map cm
              ON cm.legacy_object_type = 'category' AND cm.legacy_id = c.id
            ON CONFLICT (legacy_object_type, legacy_id) DO NOTHING
            """
        )
    )
    bind.execute(
        sa.text(
            """
            INSERT INTO quiz_migration_issue (
              library_id, severity, status, issue_code, legacy_object_type,
              legacy_id, original_path, resolution
            )
            SELECT q.library_id, 'blocking', 'open',
                   'duplicate_question_stem_in_library', 'question', q.id,
                   COALESCE(mm.original_path, '[]'::jsonb),
                   '同一题库内存在重复规范化题干，请合并或改写后重新保留题干'
            FROM quiz_question q
            LEFT JOIN quiz_legacy_migration_map mm
              ON mm.legacy_object_type = 'question' AND mm.legacy_id = q.id
            WHERE q.stem_reserved = false
            ON CONFLICT (issue_code, legacy_object_type, legacy_id) DO NOTHING
            """
        )
    )
    bind.execute(
        sa.text(
            """
            INSERT INTO quiz_migration_issue (
              library_id, severity, status, issue_code, legacy_object_type,
              legacy_id, original_path, resolution
            )
            SELECT mm.library_id, 'blocking', 'open', mm.issue_code,
                   'question', mm.legacy_id, mm.original_path,
                   CASE mm.issue_code
                     WHEN 'question_attached_to_library'
                       THEN '已迁入系统待整理模块和未分类知识点，请人工归类'
                     WHEN 'question_attached_to_module'
                       THEN '已迁入模块下系统未分类知识点，请人工归类'
                     ELSE '已迁入迁移隔离区，请人工核对原始结构'
                   END
            FROM quiz_legacy_migration_map mm
            WHERE mm.legacy_object_type = 'question' AND mm.issue_code IS NOT NULL
            ON CONFLICT (issue_code, legacy_object_type, legacy_id) DO NOTHING
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE quiz_library l
            SET migration_state = 'needs_organization'
            WHERE EXISTS (
              SELECT 1 FROM quiz_migration_issue i
              WHERE i.library_id = l.id AND i.status = 'open' AND i.severity = 'blocking'
            )
            """
        )
    )


def upgrade() -> None:
    _create_fixed_hierarchy()
    _expand_question_and_session_tables()
    _create_entitlement_and_migration_tables()
    _install_immutability_guards()
    _backfill_legacy_tree()


def _require_safe_downgrade() -> None:
    if context.is_offline_mode():
        if not os.getenv("QUIZ_V2_DOWNGRADE_BACKUP_REF", "").strip():
            raise RuntimeError(
                "offline quiz V2 downgrade requires QUIZ_V2_DOWNGRADE_BACKUP_REF"
            )
        return
    bind = op.get_bind()
    unsafe = bind.execute(
        sa.text(
            """
            SELECT
              EXISTS (SELECT 1 FROM quiz_library WHERE v2_enabled = true)
              OR EXISTS (SELECT 1 FROM quiz_course_library_binding)
              OR EXISTS (SELECT 1 FROM quiz_library_entitlement)
              OR EXISTS (
                SELECT 1 FROM quiz_practice_session WHERE scope_type IS NOT NULL
              )
              OR EXISTS (SELECT 1 FROM quiz_question WHERE category_id IS NULL)
              OR EXISTS (
                SELECT 1 FROM quiz_library l
                WHERE l.system_kind = 'none'
                  AND NOT EXISTS (
                    SELECT 1 FROM quiz_legacy_migration_map m
                    WHERE m.target_object_type = 'library' AND m.target_id = l.id
                  )
              )
            """
        )
    ).scalar()
    if unsafe and not os.getenv("QUIZ_V2_DOWNGRADE_BACKUP_REF", "").strip():
        raise RuntimeError(
            "quiz V2 contains post-migration data; verify a backup and set "
            "QUIZ_V2_DOWNGRADE_BACKUP_REF before downgrade"
        )


def downgrade() -> None:
    _require_safe_downgrade()
    op.execute("DROP TRIGGER IF EXISTS trg_quiz_question_revision_content_immutable ON quiz_question_revision")
    op.execute("DROP FUNCTION IF EXISTS quiz_question_revision_content_immutable()")
    op.execute("DROP TRIGGER IF EXISTS trg_quiz_library_code_immutable ON quiz_library")
    op.execute("DROP FUNCTION IF EXISTS quiz_library_code_immutable()")

    op.drop_constraint("fk_quiz_import_job_library", "quiz_import_job", type_="foreignkey")
    op.drop_column("quiz_import_job", "library_id")

    op.drop_constraint(
        "ck_quiz_practice_question_skip_count",
        "quiz_practice_session_question",
        type_="check",
    )
    op.drop_constraint(
        "fk_quiz_practice_question_revision",
        "quiz_practice_session_question",
        type_="foreignkey",
    )
    op.drop_column("quiz_practice_session_question", "skip_count")
    op.drop_column("quiz_practice_session_question", "withdrawn_at")
    op.drop_column("quiz_practice_session_question", "question_revision_id")

    op.drop_index("uq_quiz_practice_session_active_scope", table_name="quiz_practice_session")
    op.drop_index("uq_quiz_practice_session_active_user", table_name="quiz_practice_session")
    for name in (
        "ck_quiz_practice_session_scope_type",
        "ck_quiz_practice_session_paused_seconds",
        "ck_quiz_practice_session_lifecycle",
        "ck_quiz_practice_session_actual_count",
        "ck_quiz_practice_session_request",
        "ck_quiz_practice_session_status",
        "ck_quiz_practice_session_mode",
    ):
        op.drop_constraint(name, "quiz_practice_session", type_="check")
    op.drop_constraint(
        "fk_quiz_practice_session_library", "quiz_practice_session", type_="foreignkey"
    )
    for column in (
        "termination_reason",
        "terminated_at",
        "expired_at",
        "paused_seconds",
        "pause_reason",
        "paused_at",
        "expires_at",
        "last_answered_at",
        "scope_id",
        "scope_type",
        "library_id",
    ):
        op.drop_column("quiz_practice_session", column)
    op.create_check_constraint(
        "ck_quiz_practice_session_mode",
        "quiz_practice_session",
        "mode IN ('normal', 'wrong')",
    )
    op.create_check_constraint(
        "ck_quiz_practice_session_status",
        "quiz_practice_session",
        "status IN ('in_progress', 'completed', 'abandoned')",
    )
    op.create_check_constraint(
        "ck_quiz_practice_session_request",
        "quiz_practice_session",
        "((mode = 'normal' AND category_id IS NOT NULL AND requested_count BETWEEN 10 AND 100) "
        "OR (mode = 'wrong' AND category_id IS NULL AND requested_count = 20))",
    )
    op.create_check_constraint(
        "ck_quiz_practice_session_actual_count",
        "quiz_practice_session",
        "actual_count BETWEEN 1 AND 100",
    )
    op.create_check_constraint(
        "ck_quiz_practice_session_lifecycle",
        "quiz_practice_session",
        "((status = 'in_progress' AND completed_at IS NULL AND abandoned_at IS NULL) OR "
        "(status = 'completed' AND completed_at IS NOT NULL AND abandoned_at IS NULL) OR "
        "(status = 'abandoned' AND completed_at IS NULL AND abandoned_at IS NOT NULL))",
    )
    op.create_index(
        "uq_quiz_practice_session_active_user",
        "quiz_practice_session",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'in_progress'"),
    )

    op.drop_constraint("fk_quiz_question_pending_revision", "quiz_question", type_="foreignkey")
    op.drop_constraint("fk_quiz_question_current_revision", "quiz_question", type_="foreignkey")
    op.drop_column("quiz_question", "pending_revision_id")
    op.drop_column("quiz_question", "current_revision_id")
    op.drop_table("quiz_migration_issue")
    op.drop_table("quiz_legacy_migration_map")
    op.drop_table("quiz_library_entitlement")
    op.drop_table("quiz_course_library_binding")
    op.drop_table("quiz_question_revision")

    op.drop_index("ix_quiz_question_v2_pool", table_name="quiz_question")
    op.drop_index("uq_quiz_question_library_text_hash", table_name="quiz_question")
    op.drop_constraint("ck_quiz_question_v2_assignment", "quiz_question", type_="check")
    op.drop_constraint("ck_quiz_question_lifecycle", "quiz_question", type_="check")
    op.drop_constraint("ck_quiz_question_status", "quiz_question", type_="check")
    op.drop_constraint(
        "fk_quiz_question_knowledge_point_library", "quiz_question", type_="foreignkey"
    )
    op.drop_constraint("fk_quiz_question_library", "quiz_question", type_="foreignkey")
    for column in (
        "stem_reserved",
        "restore_until",
        "deleted_at",
        "knowledge_point_id",
        "library_id",
    ):
        op.drop_column("quiz_question", column)
    op.create_check_constraint(
        "ck_quiz_question_status",
        "quiz_question",
        "status IN ('draft', 'published', 'disabled')",
    )
    op.create_check_constraint(
        "ck_quiz_question_lifecycle",
        "quiz_question",
        "((status = 'draft' AND ever_published = false AND published_at IS NULL "
        "AND disabled_at IS NULL) OR "
        "(status = 'published' AND ever_published = true AND published_at IS NOT NULL) OR "
        "(status = 'disabled' AND ever_published = true AND published_at IS NOT NULL "
        "AND disabled_at IS NOT NULL))",
    )
    op.alter_column(
        "quiz_question",
        "category_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )

    op.drop_table("quiz_knowledge_point")
    op.drop_table("quiz_module")
    op.drop_table("quiz_library")
    op.execute("DROP FUNCTION IF EXISTS quiz_library_code()")
