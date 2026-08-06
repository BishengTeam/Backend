"""rebuild the quiz domain from the frozen schema

Revision ID: quiz001
Revises: rsh001
Create Date: 2026-08-06 00:00:00.000000
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "quiz001"
down_revision: str | Sequence[str] | None = "rsh001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LEGACY_TABLES = (
    "quiz_record",
    "quiz_checkin",
    "quiz_exam",
    "quiz_question",
    "quiz_category",
)

NEW_TABLES = (
    "quiz_admin_audit_log",
    "quiz_import_job",
    "quiz_question_stats",
    "quiz_user_stats",
    "quiz_exam_answer",
    "quiz_exam_question",
    "quiz_exam",
    "quiz_checkin",
    "quiz_collection",
    "quiz_wrong_item",
    "quiz_practice_attempt",
    "quiz_practice_session_question",
    "quiz_practice_session",
    "quiz_question",
    "quiz_category",
)


def _timestamps(*, bigint_id: bool = True) -> list[sa.Column]:
    id_type = sa.BigInteger() if bigint_id else sa.Integer()
    return [
        sa.Column("id", id_type, autoincrement=True, nullable=False),
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


def _backup_reference(name: str, environment_name: str) -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    return (x_args.get(name) or os.getenv(environment_name, "")).strip()


def _tables_have_rows(bind: sa.Connection, table_names: Sequence[str]) -> bool:
    existing = set(sa.inspect(bind).get_table_names())
    for table_name in table_names:
        if table_name not in existing:
            continue
        if bind.execute(sa.text(f'SELECT 1 FROM "{table_name}" LIMIT 1')).first():
            return True
    return False


def _require_backup_for_destructive_change(
    table_names: Sequence[str],
    *,
    argument_name: str,
    environment_name: str,
) -> None:
    backup_ref = _backup_reference(argument_name, environment_name)
    if context.is_offline_mode():
        if not backup_ref:
            raise RuntimeError(
                f"offline destructive quiz migration requires -x {argument_name}=<verified-backup-reference>"
            )
        return
    bind = op.get_bind()
    if _tables_have_rows(bind, table_names) and not backup_ref:
        raise RuntimeError(
            "quiz tables contain data; verify a database backup and rerun with "
            f"-x {argument_name}=<verified-backup-reference>"
        )


def upgrade() -> None:
    _require_backup_for_destructive_change(
        LEGACY_TABLES,
        argument_name="quiz_backup_ref",
        environment_name="QUIZ_DESTRUCTIVE_MIGRATION_BACKUP_REF",
    )

    for table_name in LEGACY_TABLES:
        op.drop_table(table_name)

    op.create_table(
        "quiz_category",
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("normalized_name", sa.String(length=128), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("depth", sa.SmallInteger(), nullable=False),
        sa.Column("description", sa.String(length=256), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "ever_had_question", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("depth BETWEEN 1 AND 3", name="ck_quiz_category_depth"),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')", name="ck_quiz_category_status"
        ),
        sa.CheckConstraint("lock_version >= 1", name="ck_quiz_category_lock_version"),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["quiz_category.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["admin_user.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["admin_user.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_quiz_category_root_name",
        "quiz_category",
        ["normalized_name"],
        unique=True,
        postgresql_where=sa.text("parent_id IS NULL"),
    )
    op.create_index(
        "uq_quiz_category_sibling_name",
        "quiz_category",
        ["parent_id", "normalized_name"],
        unique=True,
        postgresql_where=sa.text("parent_id IS NOT NULL"),
    )
    op.create_index(
        "ix_quiz_category_parent_sort",
        "quiz_category",
        ["parent_id", "sort_order", "id"],
    )

    op.create_table(
        "quiz_question",
        sa.Column("category_id", sa.BigInteger(), nullable=False),
        sa.Column("question_type", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="draft", nullable=False),
        sa.Column("question_text", sa.String(length=1024), nullable=False),
        sa.Column("normalized_question_text", sa.String(length=1024), nullable=False),
        sa.Column("question_text_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("correct_answer", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("explanation", sa.String(length=1024), nullable=True),
        sa.Column(
            "ever_published", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "question_type IN ('single_choice', 'multiple_choice', 'judge')",
            name="ck_quiz_question_type",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'disabled')",
            name="ck_quiz_question_status",
        ),
        sa.CheckConstraint(
            "((status = 'draft' AND ever_published = false "
            "AND published_at IS NULL AND disabled_at IS NULL) OR "
            "(status = 'published' AND ever_published = true "
            "AND published_at IS NOT NULL) OR "
            "(status = 'disabled' AND ever_published = true "
            "AND published_at IS NOT NULL AND disabled_at IS NOT NULL))",
            name="ck_quiz_question_lifecycle",
        ),
        sa.CheckConstraint("lock_version >= 1", name="ck_quiz_question_lock_version"),
        sa.ForeignKeyConstraint(
            ["category_id"], ["quiz_category.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["admin_user.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["admin_user.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "category_id",
            "question_text_hash",
            name="uq_quiz_question_category_text_hash",
        ),
    )
    op.create_index(
        "ix_quiz_question_pool",
        "quiz_question",
        ["category_id", "status", "question_type", "id"],
    )
    op.create_index(
        "ix_quiz_question_updated", "quiz_question", ["updated_at", "id"]
    )

    op.create_table(
        "quiz_practice_session",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("category_id", sa.BigInteger(), nullable=True),
        sa.Column("requested_count", sa.Integer(), nullable=False),
        sa.Column("actual_count", sa.Integer(), nullable=False),
        sa.Column(
            "status", sa.String(length=16), server_default="in_progress", nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("abandoned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "mode IN ('normal', 'wrong')", name="ck_quiz_practice_session_mode"
        ),
        sa.CheckConstraint(
            "status IN ('in_progress', 'completed', 'abandoned')",
            name="ck_quiz_practice_session_status",
        ),
        sa.CheckConstraint(
            "((mode = 'normal' AND category_id IS NOT NULL "
            "AND requested_count BETWEEN 10 AND 100) OR "
            "(mode = 'wrong' AND category_id IS NULL AND requested_count = 20))",
            name="ck_quiz_practice_session_request",
        ),
        sa.CheckConstraint(
            "actual_count BETWEEN 1 AND 100",
            name="ck_quiz_practice_session_actual_count",
        ),
        sa.CheckConstraint(
            "((status = 'in_progress' AND completed_at IS NULL AND abandoned_at IS NULL) OR "
            "(status = 'completed' AND completed_at IS NOT NULL AND abandoned_at IS NULL) OR "
            "(status = 'abandoned' AND completed_at IS NULL AND abandoned_at IS NOT NULL))",
            name="ck_quiz_practice_session_lifecycle",
        ),
        sa.CheckConstraint(
            "lock_version >= 1", name="ck_quiz_practice_session_lock_version"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["category_id"], ["quiz_category.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_quiz_practice_session_active_user",
        "quiz_practice_session",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'in_progress'"),
    )
    op.create_index(
        "ix_quiz_practice_session_user_history",
        "quiz_practice_session",
        ["user_id", "started_at", "id"],
    )

    op.create_table(
        "quiz_practice_session_question",
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("question_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "category_path", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("question_type", sa.String(length=24), nullable=False),
        sa.Column("question_text", sa.String(length=1024), nullable=False),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "correct_answer", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("explanation", sa.String(length=1024), nullable=False),
        sa.Column("question_lock_version", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("position >= 1", name="ck_quiz_practice_question_position"),
        sa.CheckConstraint(
            "question_type IN ('single_choice', 'multiple_choice', 'judge')",
            name="ck_quiz_practice_question_type",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["quiz_practice_session.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["question_id"], ["quiz_question.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id", "position", name="uq_quiz_practice_question_position"
        ),
        sa.UniqueConstraint(
            "session_id", "question_id", name="uq_quiz_practice_question_question"
        ),
        sa.UniqueConstraint(
            "session_id", "id", name="uq_quiz_practice_question_session_id"
        ),
    )
    op.create_index(
        "ix_quiz_practice_question_session",
        "quiz_practice_session_question",
        ["session_id", "position"],
    )

    op.create_table(
        "quiz_practice_attempt",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("session_question_id", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("is_first_attempt", sa.Boolean(), nullable=False),
        sa.Column("user_answer", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("attempt_no >= 1", name="ck_quiz_practice_attempt_no"),
        sa.CheckConstraint(
            "is_first_attempt = (attempt_no = 1)",
            name="ck_quiz_practice_attempt_first",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["session_id", "session_question_id"],
            [
                "quiz_practice_session_question.session_id",
                "quiz_practice_session_question.id",
            ],
            name="fk_quiz_practice_attempt_session_question",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "session_id",
            "idempotency_key",
            name="uq_quiz_practice_attempt_idempotency",
        ),
        sa.UniqueConstraint(
            "session_question_id",
            "attempt_no",
            name="uq_quiz_practice_attempt_number",
        ),
    )
    op.create_index(
        "ix_quiz_practice_attempt_user_time",
        "quiz_practice_attempt",
        ["user_id", "submitted_at", "id"],
    )
    op.create_index(
        "ix_quiz_practice_attempt_session_question",
        "quiz_practice_attempt",
        ["session_id", "session_question_id"],
    )

    op.create_table(
        "quiz_wrong_item",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("first_wrong_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latest_wrong_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "latest_wrong_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('active', 'cleared')", name="ck_quiz_wrong_item_status"
        ),
        sa.CheckConstraint(
            "(status = 'active' OR "
            "(status = 'cleared' AND cleared_at IS NOT NULL))",
            name="ck_quiz_wrong_item_lifecycle",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["question_id"], ["quiz_question.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "question_id", name="uq_quiz_wrong_item_user_question"
        ),
    )
    op.create_index(
        "ix_quiz_wrong_item_recent",
        "quiz_wrong_item",
        ["user_id", "status", "latest_wrong_at", "id"],
    )

    op.create_table(
        "quiz_collection",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "(is_active = true OR removed_at IS NOT NULL)",
            name="ck_quiz_collection_lifecycle",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["question_id"], ["quiz_question.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "question_id", name="uq_quiz_collection_user_question"
        ),
    )
    op.create_index(
        "ix_quiz_collection_active",
        "quiz_collection",
        ["user_id", "is_active", "collected_at", "id"],
    )

    op.create_table(
        "quiz_checkin",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("checkin_date", sa.Date(), nullable=False),
        sa.Column("questions_completed", sa.Integer(), nullable=False),
        sa.Column("consecutive_days", sa.Integer(), nullable=False),
        sa.Column("first_attempt_id", sa.BigInteger(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "questions_completed >= 1", name="ck_quiz_checkin_questions_completed"
        ),
        sa.CheckConstraint(
            "consecutive_days >= 1", name="ck_quiz_checkin_consecutive_days"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["first_attempt_id"], ["quiz_practice_attempt.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "checkin_date", name="uq_quiz_checkin_user_date"
        ),
        sa.UniqueConstraint(
            "first_attempt_id", name="uq_quiz_checkin_first_attempt"
        ),
    )
    op.create_table(
        "quiz_exam",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.BigInteger(), nullable=False),
        sa.Column("question_count", sa.Integer(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), server_default="3600", nullable=False),
        sa.Column(
            "status", sa.String(length=16), server_default="in_progress", nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timed_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("abandoned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("correct_count", sa.Integer(), nullable=True),
        sa.Column("wrong_count", sa.Integer(), nullable=True),
        sa.Column("unanswered_count", sa.Integer(), nullable=True),
        sa.Column("score", sa.Numeric(precision=5, scale=1), nullable=True),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('in_progress', 'completed', 'timed_out', 'abandoned')",
            name="ck_quiz_exam_status",
        ),
        sa.CheckConstraint(
            "question_count BETWEEN 10 AND 100", name="ck_quiz_exam_question_count"
        ),
        sa.CheckConstraint("duration_seconds = 3600", name="ck_quiz_exam_duration"),
        sa.CheckConstraint(
            "deadline_at = started_at + INTERVAL '3600 seconds'",
            name="ck_quiz_exam_deadline",
        ),
        sa.CheckConstraint("lock_version >= 1", name="ck_quiz_exam_lock_version"),
        sa.CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 100)",
            name="ck_quiz_exam_score",
        ),
        sa.CheckConstraint(
            "((status IN ('in_progress', 'abandoned') AND correct_count IS NULL "
            "AND wrong_count IS NULL AND unanswered_count IS NULL AND score IS NULL) OR "
            "(status IN ('completed', 'timed_out') AND correct_count IS NOT NULL "
            "AND wrong_count IS NOT NULL AND unanswered_count IS NOT NULL "
            "AND score IS NOT NULL AND correct_count >= 0 AND wrong_count >= 0 "
            "AND unanswered_count >= 0 AND "
            "correct_count + wrong_count + unanswered_count = question_count))",
            name="ck_quiz_exam_result_state",
        ),
        sa.CheckConstraint(
            "((status = 'in_progress' AND submitted_at IS NULL AND timed_out_at IS NULL "
            "AND abandoned_at IS NULL) OR "
            "(status = 'completed' AND submitted_at IS NOT NULL AND timed_out_at IS NULL "
            "AND abandoned_at IS NULL) OR "
            "(status = 'timed_out' AND submitted_at IS NULL AND timed_out_at IS NOT NULL "
            "AND abandoned_at IS NULL) OR "
            "(status = 'abandoned' AND submitted_at IS NULL AND timed_out_at IS NULL "
            "AND abandoned_at IS NOT NULL))",
            name="ck_quiz_exam_lifecycle",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["category_id"], ["quiz_category.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_quiz_exam_active_user",
        "quiz_exam",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'in_progress'"),
    )
    op.create_index(
        "ix_quiz_exam_deadline", "quiz_exam", ["status", "deadline_at", "id"]
    )
    op.create_index(
        "ix_quiz_exam_user_history",
        "quiz_exam",
        ["user_id", "started_at", "id"],
    )

    op.create_table(
        "quiz_exam_question",
        sa.Column("exam_id", sa.BigInteger(), nullable=False),
        sa.Column("question_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "category_path", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("question_type", sa.String(length=24), nullable=False),
        sa.Column("question_text", sa.String(length=1024), nullable=False),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "correct_answer", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("explanation", sa.String(length=1024), nullable=False),
        sa.Column("question_lock_version", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("position >= 1", name="ck_quiz_exam_question_position"),
        sa.CheckConstraint(
            "question_type IN ('single_choice', 'multiple_choice', 'judge')",
            name="ck_quiz_exam_question_type",
        ),
        sa.ForeignKeyConstraint(["exam_id"], ["quiz_exam.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["question_id"], ["quiz_question.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "exam_id", "position", name="uq_quiz_exam_question_position"
        ),
        sa.UniqueConstraint(
            "exam_id", "question_id", name="uq_quiz_exam_question_question"
        ),
        sa.UniqueConstraint("exam_id", "id", name="uq_quiz_exam_question_exam_id"),
    )
    op.create_index(
        "ix_quiz_exam_question_exam",
        "quiz_exam_question",
        ["exam_id", "position"],
    )

    op.create_table(
        "quiz_exam_answer",
        sa.Column("exam_id", sa.BigInteger(), nullable=False),
        sa.Column("exam_question_id", sa.BigInteger(), nullable=False),
        sa.Column("user_answer", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=True),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        *_timestamps(),
        sa.CheckConstraint("lock_version >= 1", name="ck_quiz_exam_answer_lock_version"),
        sa.ForeignKeyConstraint(
            ["exam_id", "exam_question_id"],
            ["quiz_exam_question.exam_id", "quiz_exam_question.id"],
            name="fk_quiz_exam_answer_exam_question",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "exam_question_id", name="uq_quiz_exam_answer_question"
        ),
    )
    op.create_index(
        "ix_quiz_exam_answer_exam",
        "quiz_exam_answer",
        ["exam_id", "exam_question_id"],
    )

    op.create_table(
        "quiz_user_stats",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("practice_total_attempts", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("practice_first_attempts", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("practice_first_correct", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "practice_answered_questions", sa.BigInteger(), server_default="0", nullable=False
        ),
        sa.Column("active_wrong_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "active_collection_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("checkin_days", sa.Integer(), server_default="0", nullable=False),
        sa.Column("consecutive_days", sa.Integer(), server_default="0", nullable=False),
        sa.Column("today_practice_date", sa.Date(), nullable=True),
        sa.Column("today_practice_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("completed_exam_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("timed_out_exam_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("exam_total_questions", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("exam_correct", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("exam_wrong", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("exam_unanswered", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "exam_score_sum", sa.Numeric(precision=14, scale=1), server_default="0", nullable=False
        ),
        sa.Column("exam_high_score", sa.Numeric(precision=5, scale=1), nullable=True),
        sa.Column("exam_latest_score", sa.Numeric(precision=5, scale=1), nullable=True),
        sa.Column("exam_latest_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "practice_total_attempts >= 0 AND practice_first_attempts >= 0 "
            "AND practice_first_correct >= 0 AND practice_answered_questions >= 0 "
            "AND active_wrong_count >= 0 AND active_collection_count >= 0 "
            "AND checkin_days >= 0 AND consecutive_days >= 0 "
            "AND today_practice_count >= 0 AND completed_exam_count >= 0 "
            "AND timed_out_exam_count >= 0 AND exam_total_questions >= 0 "
            "AND exam_correct >= 0 AND exam_wrong >= 0 AND exam_unanswered >= 0 "
            "AND exam_score_sum >= 0 AND practice_first_correct <= practice_first_attempts "
            "AND exam_correct + exam_wrong + exam_unanswered = exam_total_questions "
            "AND (exam_high_score IS NULL OR (exam_high_score >= 0 AND exam_high_score <= 100)) "
            "AND (exam_latest_score IS NULL OR (exam_latest_score >= 0 AND exam_latest_score <= 100))",
            name="ck_quiz_user_stats_nonnegative",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_quiz_user_stats_user"),
    )

    op.create_table(
        "quiz_question_stats",
        sa.Column("question_id", sa.BigInteger(), nullable=False),
        sa.Column("practice_first_attempts", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("practice_first_correct", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("exam_answers", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("exam_correct", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("aggregated_through", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "practice_first_attempts >= 0 AND practice_first_correct >= 0 "
            "AND exam_answers >= 0 AND exam_correct >= 0 "
            "AND practice_first_correct <= practice_first_attempts "
            "AND exam_correct <= exam_answers",
            name="ck_quiz_question_stats_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["question_id"], ["quiz_question.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "question_id", name="uq_quiz_question_stats_question"
        ),
    )

    op.create_table(
        "quiz_import_job",
        sa.Column("admin_id", sa.Integer(), nullable=True),
        sa.Column("import_batch_key", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="queued", nullable=False),
        sa.Column("source_object_key", sa.String(length=512), nullable=False),
        sa.Column("source_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("report_object_key", sa.String(length=512), nullable=True),
        sa.Column("total_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("validated_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_message", sa.String(length=1024), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "source_type IN ('csv', 'json')", name="ck_quiz_import_source_type"
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'validating', 'validation_failed', 'importing', "
            "'succeeded', 'failed')",
            name="ck_quiz_import_status",
        ),
        sa.CheckConstraint(
            "source_size_bytes BETWEEN 1 AND 10485760",
            name="ck_quiz_import_source_size",
        ),
        sa.CheckConstraint(
            "total_rows BETWEEN 0 AND 5000 AND validated_rows BETWEEN 0 AND 5000 "
            "AND created_count BETWEEN 0 AND 5000 AND error_count >= 0 "
            "AND validated_rows <= total_rows AND created_count <= total_rows",
            name="ck_quiz_import_counts",
        ),
        sa.CheckConstraint("retry_count >= 0", name="ck_quiz_import_retry_count"),
        sa.ForeignKeyConstraint(
            ["admin_id"], ["admin_user.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("import_batch_key", name="uq_quiz_import_batch_key"),
    )
    op.create_index(
        "ix_quiz_import_job_queue",
        "quiz_import_job",
        ["status", "created_at", "id"],
    )
    op.create_index(
        "ix_quiz_import_job_admin",
        "quiz_import_job",
        ["admin_id", "created_at", "id"],
    )
    op.create_index(
        "ix_quiz_import_job_expiry",
        "quiz_import_job",
        ["expires_at", "id"],
    )

    op.create_table(
        "quiz_admin_audit_log",
        sa.Column("actor_type", sa.String(length=16), nullable=False),
        sa.Column("admin_id", sa.Integer(), nullable=True),
        sa.Column("permission", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("object_type", sa.String(length=64), nullable=False),
        sa.Column("object_id", sa.BigInteger(), nullable=True),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column(
            "changed_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("target_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        *_created_at_columns(),
        sa.CheckConstraint(
            "actor_type IN ('admin', 'system')", name="ck_quiz_audit_actor_type"
        ),
        sa.CheckConstraint(
            "result IN ('succeeded', 'failed')", name="ck_quiz_audit_result"
        ),
        sa.CheckConstraint(
            "((actor_type = 'admin' AND admin_id IS NOT NULL) OR "
            "(actor_type = 'system' AND admin_id IS NULL))",
            name="ck_quiz_audit_actor",
        ),
        sa.ForeignKeyConstraint(
            ["admin_id"], ["admin_user.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_quiz_audit_object",
        "quiz_admin_audit_log",
        ["object_type", "object_id", "created_at"],
    )
    op.create_index(
        "ix_quiz_audit_actor",
        "quiz_admin_audit_log",
        ["actor_type", "admin_id", "created_at"],
    )
    op.create_index(
        "ix_quiz_audit_action",
        "quiz_admin_audit_log",
        ["action", "created_at"],
    )


def downgrade() -> None:
    _require_backup_for_destructive_change(
        NEW_TABLES,
        argument_name="quiz_downgrade_backup_ref",
        environment_name="QUIZ_DESTRUCTIVE_DOWNGRADE_BACKUP_REF",
    )

    for table_name in NEW_TABLES:
        op.drop_table(table_name)

    op.create_table(
        "quiz_category",
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("description", sa.String(length=256), nullable=True),
        *_timestamps(bigint_id=False),
        sa.ForeignKeyConstraint(["parent_id"], ["quiz_category.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_quiz_category_parent_id"),
        "quiz_category",
        ["parent_id"],
    )

    op.create_table(
        "quiz_checkin",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("checkin_date", sa.Date(), nullable=False),
        sa.Column("questions_completed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("consecutive_days", sa.Integer(), server_default="0", nullable=False),
        *_timestamps(bigint_id=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_quiz_checkin_user_id"), "quiz_checkin", ["user_id"]
    )
    op.create_index(
        "uq_quiz_checkin_user_date",
        "quiz_checkin",
        ["user_id", "checkin_date"],
        unique=True,
    )

    op.create_table(
        "quiz_question",
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("question_type", sa.String(length=16), nullable=False),
        sa.Column("question_text", sa.String(length=1024), nullable=False),
        sa.Column("options", sa.JSON(), nullable=True),
        sa.Column("correct_answer", sa.String(length=256), nullable=False),
        sa.Column("explanation", sa.String(length=1024), nullable=True),
        *_timestamps(bigint_id=False),
        sa.CheckConstraint(
            "question_type IN ('single_choice', 'multiple_choice', 'judge')",
            name="ck_quiz_question_type",
        ),
        sa.ForeignKeyConstraint(["category_id"], ["quiz_category.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_quiz_question_category_id"),
        "quiz_question",
        ["category_id"],
    )

    op.create_table(
        "quiz_exam",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("question_ids", sa.JSON(), nullable=False),
        sa.Column("answers", sa.JSON(), nullable=True),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("correct_count", sa.Integer(), nullable=True),
        sa.Column("wrong_count", sa.Integer(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("elapsed_seconds", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status", sa.String(length=16), server_default="in_progress", nullable=False
        ),
        *_timestamps(bigint_id=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_quiz_exam_user_id"), "quiz_exam", ["user_id"])

    op.create_table(
        "quiz_record",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("user_answer", sa.String(length=256), nullable=True),
        sa.Column("is_correct", sa.Boolean(), nullable=True),
        sa.Column("is_collected", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_wrong", sa.Boolean(), server_default="false", nullable=False),
        *_timestamps(bigint_id=False),
        sa.ForeignKeyConstraint(["question_id"], ["quiz_question.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_quiz_record_user_id"), "quiz_record", ["user_id"])
    op.create_index(
        "uq_quiz_record_user_question",
        "quiz_record",
        ["user_id", "question_id"],
        unique=True,
    )
