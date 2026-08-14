"""add V2 practice review fields and revision-level statistics

Revision ID: quiz005
Revises: quiz004
Create Date: 2026-08-13 13:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "quiz005"
down_revision: str | Sequence[str] | None = "quiz004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

V2_PRACTICE_TABLES = ("quiz_question_revision_stats",)


def upgrade() -> None:
    op.add_column(
        "quiz_wrong_item",
        sa.Column("review_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "quiz_wrong_item",
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_quiz_wrong_item_review_count",
        "quiz_wrong_item",
        "review_count >= 0",
    )

    op.alter_column(
        "quiz_practice_session_question",
        "category_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )

    op.create_table(
        "quiz_question_revision_stats",
        sa.Column("question_id", sa.BigInteger(), nullable=False),
        sa.Column("question_revision_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "practice_first_attempts",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "practice_first_correct",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("exam_answers", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("exam_correct", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("aggregated_through", sa.DateTime(timezone=True), nullable=True),
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
            "practice_first_attempts >= 0 AND practice_first_correct >= 0 "
            "AND exam_answers >= 0 AND exam_correct >= 0 "
            "AND practice_first_correct <= practice_first_attempts "
            "AND exam_correct <= exam_answers",
            name="ck_quiz_question_revision_stats_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["question_id"],
            ["quiz_question.id"],
            name="fk_quiz_question_revision_stats_question",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["question_revision_id"],
            ["quiz_question_revision.id"],
            name="fk_quiz_question_revision_stats_revision",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "question_revision_id",
            name="uq_quiz_question_revision_stats_revision",
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    has_v2_snapshots = bind.execute(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM quiz_practice_session_question "
            "WHERE category_id IS NULL)"
        )
    ).scalar()
    if has_v2_snapshots:
        raise RuntimeError(
            "quiz005 contains V2 practice snapshots without a legacy category; "
            "remove them only after verifying a backup before downgrade"
        )

    op.drop_table("quiz_question_revision_stats")
    op.alter_column(
        "quiz_practice_session_question",
        "category_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    op.drop_constraint(
        "ck_quiz_wrong_item_review_count",
        "quiz_wrong_item",
        type_="check",
    )
    op.drop_column("quiz_wrong_item", "last_reviewed_at")
    op.drop_column("quiz_wrong_item", "review_count")
