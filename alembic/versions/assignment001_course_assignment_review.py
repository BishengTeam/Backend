"""Add course assignments and manual essay review

Revision ID: assignment001
Revises: cp009_reactivate_h3c
Create Date: 2026-08-31 16:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "assignment001"
down_revision: str | Sequence[str] | None = "cp009_reactivate_h3c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ASSIGNMENT_TABLES = (
    "quiz_course_assignment",
    "quiz_course_assignment_question",
    "quiz_course_assignment_version",
    "quiz_course_assignment_submission",
    "quiz_course_assignment_answer",
    "quiz_course_assignment_submission_question",
    "quiz_course_assignment_review_log",
)


def _drop_type_constraints() -> None:
    op.drop_constraint(
        "ck_quiz_question_type", "quiz_question", type_="check"
    )
    op.drop_constraint(
        "ck_quiz_question_revision_type", "quiz_question_revision", type_="check"
    )


def _restore_type_constraints() -> None:
    op.create_check_constraint(
        "ck_quiz_question_revision_type",
        "quiz_question_revision",
        "question_type IN ('single_choice', 'multiple_choice', 'judge')",
    )
    op.create_check_constraint(
        "ck_quiz_question_type",
        "quiz_question",
        "question_type IN ('single_choice', 'multiple_choice', 'judge')",
    )


def upgrade() -> None:
    _drop_type_constraints()
    op.create_check_constraint(
        "ck_quiz_question_type",
        "quiz_question",
        "question_type IN ('single_choice', 'multiple_choice', 'judge', 'essay')",
    )
    op.create_check_constraint(
        "ck_quiz_question_revision_type",
        "quiz_question_revision",
        "question_type IN ('single_choice', 'multiple_choice', 'judge', 'essay')",
    )
    op.add_column(
        "quiz_question",
        sa.Column("reference_answer", sa.Text(), nullable=True),
    )
    op.add_column(
        "quiz_question_revision",
        sa.Column("reference_answer", sa.Text(), nullable=True),
    )

    op.create_table(
        "quiz_course_assignment",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("library_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("version_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("question_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("essay_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("objective_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "essay_total_score",
            sa.Numeric(7, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "objective_total_score",
            sa.Numeric(7, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "total_score", sa.Numeric(7, 2), nullable=False, server_default="100"
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["library_id"], ["quiz_library.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["admin_user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["admin_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("status IN ('draft', 'published', 'disabled')", name="ck_quiz_course_assignment_status"),
        sa.CheckConstraint("version_no >= 1", name="ck_quiz_course_assignment_version"),
        sa.CheckConstraint("lock_version >= 1", name="ck_quiz_course_assignment_lock"),
        sa.CheckConstraint(
            "total_score = 100 AND essay_total_score >= 0 "
            "AND essay_total_score <= 100 "
            "AND objective_total_score >= 0 AND objective_total_score <= 100 "
            "AND essay_total_score + objective_total_score = total_score",
            name="ck_quiz_course_assignment_score_total",
        ),
        sa.UniqueConstraint("library_id", name="uq_quiz_course_assignment_library"),
    )
    op.create_index(
        "ix_quiz_course_assignment_status",
        "quiz_course_assignment",
        ["status", "id"],
    )

    op.create_table(
        "quiz_course_assignment_question",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("assignment_id", sa.BigInteger(), nullable=False),
        sa.Column("question_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("is_essay", sa.Boolean(), nullable=False),
        sa.Column("score", sa.Numeric(7, 2), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["assignment_id"], ["quiz_course_assignment.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["question_id"], ["quiz_question.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("position >= 1", name="ck_quiz_course_assignment_position"),
        sa.CheckConstraint("score >= 0 AND score <= 100", name="ck_quiz_course_assignment_question_score"),
        sa.UniqueConstraint("assignment_id", "question_id"),
        sa.UniqueConstraint("assignment_id", "position"),
        sa.UniqueConstraint("assignment_id", "id"),
    )
    op.create_index(
        "ix_quiz_course_assignment_question_pool",
        "quiz_course_assignment_question",
        ["assignment_id", "position", "id"],
    )

    op.create_table(
        "quiz_course_assignment_version",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("assignment_id", sa.BigInteger(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("question_count", sa.Integer(), nullable=False),
        sa.Column("essay_count", sa.Integer(), nullable=False),
        sa.Column("objective_count", sa.Integer(), nullable=False),
        sa.Column("essay_total_score", sa.Numeric(7, 2), nullable=False),
        sa.Column("objective_total_score", sa.Numeric(7, 2), nullable=False),
        sa.Column("total_score", sa.Numeric(7, 2), nullable=False),
        sa.Column("config_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["assignment_id"], ["quiz_course_assignment.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["admin_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("version_no >= 1", name="ck_quiz_assignment_version_no"),
        sa.CheckConstraint("question_count >= 1", name="ck_quiz_assignment_version_count"),
        sa.CheckConstraint("total_score = 100", name="ck_quiz_assignment_version_total"),
        sa.UniqueConstraint("assignment_id", "version_no"),
        sa.UniqueConstraint("assignment_id", "id"),
    )

    op.create_table(
        "quiz_course_assignment_submission",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("assignment_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("config_version_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_by", sa.Integer(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("graded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("graded_by", sa.Integer(), nullable=True),
        sa.Column("total_score", sa.Numeric(7, 2), nullable=True),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["assignment_id"], ["quiz_course_assignment.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["claimed_by"], ["admin_user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["graded_by"], ["admin_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("status IN ('draft', 'submitted', 'claimed', 'graded')", name="ck_quiz_assignment_submission_status"),
        sa.CheckConstraint("total_score IS NULL OR (total_score >= 0 AND total_score <= 100)", name="ck_quiz_assignment_submission_score"),
        sa.CheckConstraint("lock_version >= 1", name="ck_quiz_assignment_submission_lock"),
        sa.CheckConstraint("config_version_no >= 1", name="ck_quiz_assignment_submission_version"),
        sa.UniqueConstraint("assignment_id", "user_id"),
    )
    op.create_index(
        "ix_quiz_assignment_submission_queue",
        "quiz_course_assignment_submission",
        ["assignment_id", "status", "submitted_at", "id"],
    )
    op.create_index(
        "ix_quiz_assignment_submission_user",
        "quiz_course_assignment_submission",
        ["user_id", "status", "updated_at"],
    )

    op.create_table(
        "quiz_course_assignment_answer",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("submission_id", sa.BigInteger(), nullable=False),
        sa.Column("question_id", sa.BigInteger(), nullable=False),
        sa.Column("user_answer", postgresql.JSONB(), nullable=True),
        sa.Column("is_answered", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["submission_id"], ["quiz_course_assignment_submission.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["question_id"], ["quiz_question.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("submission_id", "question_id"),
    )
    op.create_index(
        "ix_quiz_assignment_answer_question",
        "quiz_course_assignment_answer",
        ["question_id", "id"],
    )

    op.create_table(
        "quiz_course_assignment_submission_question",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("submission_id", sa.BigInteger(), nullable=False),
        sa.Column("question_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("is_essay", sa.Boolean(), nullable=False),
        sa.Column("is_answered", sa.Boolean(), nullable=False),
        sa.Column("requires_review", sa.Boolean(), nullable=False),
        sa.Column("score", sa.Numeric(7, 2), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("user_answer", postgresql.JSONB(), nullable=True),
        sa.Column("is_objective_correct", sa.Boolean(), nullable=True),
        sa.Column("manual_score", sa.Numeric(7, 2), nullable=True),
        sa.Column("review_comment", sa.Text(), nullable=True),
        sa.Column("earned_score", sa.Numeric(7, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["submission_id"], ["quiz_course_assignment_submission.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("position >= 1", name="ck_quiz_assignment_result_position"),
        sa.CheckConstraint("score >= 0 AND score <= 100", name="ck_quiz_assignment_result_score"),
        sa.CheckConstraint("manual_score IS NULL OR (manual_score >= 0 AND manual_score <= score)", name="ck_quiz_assignment_manual_score"),
        sa.CheckConstraint("earned_score IS NULL OR (earned_score >= 0 AND earned_score <= score)", name="ck_quiz_assignment_earned_score"),
        sa.UniqueConstraint("submission_id", "question_id"),
        sa.UniqueConstraint("submission_id", "position"),
        sa.UniqueConstraint("submission_id", "id"),
    )

    op.create_table(
        "quiz_course_assignment_review_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("submission_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_type", sa.String(length=16), nullable=False),
        sa.Column("admin_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("before", postgresql.JSONB(), nullable=True),
        sa.Column("after", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["submission_id"], ["quiz_course_assignment_submission.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["admin_id"], ["admin_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("action IN ('save', 'claim', 'complete', 'reopen')", name="ck_quiz_assignment_review_action"),
        sa.CheckConstraint("actor_type IN ('admin', 'system')", name="ck_quiz_assignment_review_actor"),
        sa.CheckConstraint(
            "((actor_type = 'admin' AND admin_id IS NOT NULL) OR "
            "(actor_type = 'system' AND admin_id IS NULL))",
            name="ck_quiz_assignment_review_actor_ref",
        ),
    )
    op.create_index(
        "ix_quiz_assignment_review_log_submission",
        "quiz_course_assignment_review_log",
        ["submission_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_table("quiz_course_assignment_review_log")
    op.drop_table("quiz_course_assignment_submission_question")
    op.drop_table("quiz_course_assignment_answer")
    op.drop_index("ix_quiz_assignment_submission_user", table_name="quiz_course_assignment_submission")
    op.drop_index("ix_quiz_assignment_submission_queue", table_name="quiz_course_assignment_submission")
    op.drop_table("quiz_course_assignment_submission")
    op.drop_table("quiz_course_assignment_version")
    op.drop_index("ix_quiz_course_assignment_question_pool", table_name="quiz_course_assignment_question")
    op.drop_table("quiz_course_assignment_question")
    op.drop_index("ix_quiz_course_assignment_status", table_name="quiz_course_assignment")
    op.drop_table("quiz_course_assignment")
    op.drop_column("quiz_question_revision", "reference_answer")
    op.drop_column("quiz_question", "reference_answer")
    _drop_type_constraints()
    _restore_type_constraints()
