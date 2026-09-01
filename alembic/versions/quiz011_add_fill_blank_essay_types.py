"""add fill_blank and essay question types

Revision ID: quiz011
Revises: cp009_reactivate_h3c
Create Date: 2026-09-01 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "quiz011"
down_revision: str | Sequence[str] | None = "cp009_reactivate_h3c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TYPE_CHECK = (
    "question_type IN "
    "('single_choice', 'multiple_choice', 'judge', 'fill_blank', 'essay')"
)
_OLD_TYPE_CHECK = "question_type IN ('single_choice', 'multiple_choice', 'judge')"

_CONSTRAINTS = (
    ("quiz_question", "ck_quiz_question_type"),
    ("quiz_question_revision", "ck_quiz_question_revision_type"),
    ("quiz_practice_session_question", "ck_quiz_practice_question_type"),
    ("quiz_exam_question", "ck_quiz_exam_question_type"),
)


def upgrade() -> None:
    for table, constraint in _CONSTRAINTS:
        op.drop_constraint(constraint, table, type_="check")
        op.create_check_constraint(constraint, table, _TYPE_CHECK)


def downgrade() -> None:
    for table, constraint in reversed(_CONSTRAINTS):
        op.drop_constraint(constraint, table, type_="check")
        op.create_check_constraint(constraint, table, _OLD_TYPE_CHECK)
