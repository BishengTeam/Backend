"""add final-submit answer drafts to practice snapshots

Revision ID: quiz007
Revises: quiz006
Create Date: 2026-08-14 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "quiz007"
down_revision: str | Sequence[str] | None = "quiz006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "quiz_practice_session_question",
        sa.Column("user_answer", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "quiz_practice_session_question",
        sa.Column(
            "answer_lock_version",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "quiz_practice_session_question",
        sa.Column("answer_saved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_quiz_practice_question_saved_answer",
        "quiz_practice_session_question",
        "((user_answer IS NULL AND answer_lock_version = 0 "
        "AND answer_saved_at IS NULL) OR "
        "(user_answer IS NOT NULL AND answer_lock_version >= 1 "
        "AND answer_saved_at IS NOT NULL))",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_quiz_practice_question_saved_answer",
        "quiz_practice_session_question",
        type_="check",
    )
    op.drop_column("quiz_practice_session_question", "answer_saved_at")
    op.drop_column("quiz_practice_session_question", "answer_lock_version")
    op.drop_column("quiz_practice_session_question", "user_answer")
