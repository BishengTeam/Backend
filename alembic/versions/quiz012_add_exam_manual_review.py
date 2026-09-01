"""add exam manual-review state and per-question score ratios

Revision ID: quiz012
Revises: quiz011
Create Date: 2026-09-01 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "quiz012"
down_revision: str | Sequence[str] | None = "quiz011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


REVIEW_TABLES = ("quiz_exam_question_review",)


_EXAM_RESULT_STATE = (
    "((status IN ('in_progress', 'abandoned') AND correct_count IS NULL "
    "AND wrong_count IS NULL AND unanswered_count IS NULL AND score IS NULL) OR "
    "(status IN ('completed', 'timed_out') AND review_status IN "
    "('pending', 'in_progress', 'recalled') AND correct_count IS NULL "
    "AND wrong_count IS NULL AND unanswered_count IS NULL AND score IS NULL) OR "
    "(status IN ('completed', 'timed_out') AND review_status IN "
    "('none', 'completed') AND correct_count IS NOT NULL "
    "AND wrong_count IS NOT NULL AND unanswered_count IS NOT NULL "
    "AND score IS NOT NULL AND correct_count >= 0 AND wrong_count >= 0 "
    "AND unanswered_count >= 0 AND "
    "correct_count + wrong_count + unanswered_count = question_count))"
)

_OLD_EXAM_RESULT_STATE = (
    "((status IN ('in_progress', 'abandoned') AND correct_count IS NULL "
    "AND wrong_count IS NULL AND unanswered_count IS NULL AND score IS NULL) OR "
    "(status IN ('completed', 'timed_out') AND correct_count IS NOT NULL "
    "AND wrong_count IS NOT NULL AND unanswered_count IS NOT NULL "
    "AND score IS NOT NULL AND correct_count >= 0 AND wrong_count >= 0 "
    "AND unanswered_count >= 0 AND "
    "correct_count + wrong_count + unanswered_count = question_count))"
)

_USER_STATS_NONNEGATIVE = (
    "practice_total_attempts >= 0 AND practice_first_attempts >= 0 "
    "AND practice_first_correct >= 0 AND practice_answered_questions >= 0 "
    "AND active_wrong_count >= 0 AND active_collection_count >= 0 "
    "AND checkin_days >= 0 AND consecutive_days >= 0 "
    "AND today_practice_count >= 0 AND completed_exam_count >= 0 "
    "AND timed_out_exam_count >= 0 AND exam_total_questions >= 0 "
    "AND exam_correct >= 0 AND exam_partial >= 0 "
    "AND exam_wrong >= 0 AND exam_unanswered >= 0 "
    "AND exam_score_sum >= 0 AND practice_first_correct <= practice_first_attempts "
    "AND exam_correct + exam_partial + exam_wrong + exam_unanswered "
    "= exam_total_questions "
    "AND (exam_high_score IS NULL OR (exam_high_score >= 0 AND exam_high_score <= 100)) "
    "AND (exam_latest_score IS NULL OR (exam_latest_score >= 0 AND exam_latest_score <= 100))"
)

_OLD_USER_STATS_NONNEGATIVE = (
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
    "AND (exam_latest_score IS NULL OR (exam_latest_score >= 0 AND exam_latest_score <= 100))"
)


def upgrade() -> None:
    op.add_column(
        "quiz_exam",
        sa.Column(
            "review_status",
            sa.String(length=16),
            server_default="none",
            nullable=False,
        ),
    )
    op.add_column("quiz_exam", sa.Column("review_locked_by", sa.Integer(), nullable=True))
    op.add_column(
        "quiz_exam", sa.Column("review_locked_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        "ck_quiz_exam_review_status",
        "quiz_exam",
        "review_status IN ('none', 'pending', 'in_progress', 'completed', 'recalled')",
    )
    op.drop_constraint("ck_quiz_exam_result_state", "quiz_exam", type_="check")
    op.create_check_constraint(
        "ck_quiz_exam_result_state", "quiz_exam", _EXAM_RESULT_STATE
    )
    op.create_foreign_key(
        "fk_quiz_exam_review_locked_by",
        "quiz_exam",
        "admin_user",
        ["review_locked_by"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "quiz_exam_answer",
        sa.Column("score_ratio", sa.Numeric(4, 3), nullable=True),
    )

    op.create_table(
        "quiz_exam_question_review",
        sa.Column("exam_id", sa.BigInteger(), nullable=False),
        sa.Column("exam_question_id", sa.BigInteger(), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), nullable=True),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("comment", sa.String(length=512), nullable=True),
        sa.Column(
            "status", sa.String(length=16), server_default="active", nullable=False
        ),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "lock_version", sa.Integer(), server_default="1", nullable=False
        ),
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
        sa.CheckConstraint(
            "verdict IN ('wrong', 'partial', 'correct')",
            name="ck_quiz_exam_review_verdict",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'superseded')",
            name="ck_quiz_exam_review_status",
        ),
        sa.CheckConstraint("lock_version >= 1", name="ck_quiz_exam_review_lock_version"),
        sa.ForeignKeyConstraint(
            ["exam_id", "exam_question_id"],
            ["quiz_exam_question.exam_id", "quiz_exam_question.id"],
            name="fk_quiz_exam_review_exam_question",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_id"], ["admin_user.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_quiz_exam_review_pending",
        "quiz_exam_question_review",
        ["status", "updated_at", "id"],
    )
    op.create_index(
        "uq_quiz_exam_review_active_question",
        "quiz_exam_question_review",
        ["exam_question_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )

    op.add_column(
        "quiz_user_stats",
        sa.Column(
            "exam_partial",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
    )
    op.drop_constraint(
        "ck_quiz_user_stats_nonnegative", "quiz_user_stats", type_="check"
    )
    op.create_check_constraint(
        "ck_quiz_user_stats_nonnegative", "quiz_user_stats", _USER_STATS_NONNEGATIVE
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_quiz_user_stats_nonnegative", "quiz_user_stats", type_="check"
    )
    op.create_check_constraint(
        "ck_quiz_user_stats_nonnegative",
        "quiz_user_stats",
        _OLD_USER_STATS_NONNEGATIVE,
    )
    op.drop_column("quiz_user_stats", "exam_partial")

    op.drop_index(
        "uq_quiz_exam_review_active_question", table_name="quiz_exam_question_review"
    )
    op.drop_index(
        "ix_quiz_exam_review_pending", table_name="quiz_exam_question_review"
    )
    op.drop_table("quiz_exam_question_review")

    op.drop_column("quiz_exam_answer", "score_ratio")

    op.drop_constraint("fk_quiz_exam_review_locked_by", "quiz_exam", type_="foreignkey")
    op.drop_constraint("ck_quiz_exam_result_state", "quiz_exam", type_="check")
    op.create_check_constraint(
        "ck_quiz_exam_result_state", "quiz_exam", _OLD_EXAM_RESULT_STATE
    )
    op.drop_constraint("ck_quiz_exam_review_status", "quiz_exam", type_="check")
    op.drop_column("quiz_exam", "review_locked_at")
    op.drop_column("quiz_exam", "review_locked_by")
    op.drop_column("quiz_exam", "review_status")
