"""add uncertain_choice/matching/ordering types plus question score and tags

Revision ID: quiz016
Revises: rsh007_employment_paths
Create Date: 2026-10-20 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "quiz016"
down_revision: str | Sequence[str] | None = "rsh007_employment_paths"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TYPE_CHECK = (
    "question_type IN "
    "('single_choice', 'multiple_choice', 'uncertain_choice', 'judge', "
    "'fill_blank', 'essay', 'matching', 'ordering')"
)
_OLD_TYPE_CHECK = (
    "question_type IN "
    "('single_choice', 'multiple_choice', 'judge', 'fill_blank', 'essay')"
)

_TYPE_CONSTRAINTS = (
    ("quiz_question", "ck_quiz_question_type"),
    ("quiz_question_revision", "ck_quiz_question_revision_type"),
    ("quiz_practice_session_question", "ck_quiz_practice_question_type"),
    ("quiz_exam_question", "ck_quiz_exam_question_type"),
)


def upgrade() -> None:
    for table, constraint in _TYPE_CONSTRAINTS:
        op.drop_constraint(constraint, table, type_="check")
        op.create_check_constraint(constraint, table, _TYPE_CHECK)

    op.drop_constraint(
        "ck_quiz_import_source_type", "quiz_import_job", type_="check"
    )
    op.create_check_constraint(
        "ck_quiz_import_source_type",
        "quiz_import_job",
        "source_type IN ('csv', 'json', 'xlsx')",
    )

    op.add_column(
        "quiz_question",
        sa.Column("score", sa.Numeric(5, 1), nullable=True),
    )
    op.create_check_constraint(
        "ck_quiz_question_score_range",
        "quiz_question",
        "score IS NULL OR (score >= 0 AND score <= 100)",
    )
    bind = op.get_bind()
    tags_type = (
        postgresql.JSONB(astext_type=sa.Text())
        if bind.dialect.name == "postgresql"
        else sa.JSON()
    )
    tags_default = (
        sa.text("'[]'::jsonb") if bind.dialect.name == "postgresql" else sa.text("'[]'")
    )
    op.add_column(
        "quiz_question",
        sa.Column("tags", tags_type, nullable=False, server_default=tags_default),
    )


def downgrade() -> None:
    op.drop_column("quiz_question", "tags")
    op.drop_constraint(
        "ck_quiz_question_score_range", "quiz_question", type_="check"
    )
    op.drop_column("quiz_question", "score")

    op.drop_constraint(
        "ck_quiz_import_source_type", "quiz_import_job", type_="check"
    )
    op.create_check_constraint(
        "ck_quiz_import_source_type",
        "quiz_import_job",
        "source_type IN ('csv', 'json')",
    )

    for table, constraint in reversed(_TYPE_CONSTRAINTS):
        op.drop_constraint(constraint, table, type_="check")
        op.create_check_constraint(constraint, table, _OLD_TYPE_CHECK)
