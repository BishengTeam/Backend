"""add indexes for quiz question-stat dirty scans

Revision ID: quiz002
Revises: quiz001
Create Date: 2026-08-07 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "quiz002"
down_revision: str | Sequence[str] | None = "quiz001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_quiz_practice_attempt_submitted",
        "quiz_practice_attempt",
        ["submitted_at", "id"],
    )
    op.create_index(
        "ix_quiz_exam_status_updated",
        "quiz_exam",
        ["status", "updated_at", "id"],
    )
    op.create_index(
        "ix_quiz_exam_answer_updated",
        "quiz_exam_answer",
        ["updated_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_quiz_exam_answer_updated",
        table_name="quiz_exam_answer",
    )
    op.drop_index(
        "ix_quiz_exam_status_updated",
        table_name="quiz_exam",
    )
    op.drop_index(
        "ix_quiz_practice_attempt_submitted",
        table_name="quiz_practice_attempt",
    )
