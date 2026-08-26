"""add per-option image urls to quiz questions and snapshots

Revision ID: quiz009
Revises: crs002
Create Date: 2026-08-25 21:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "quiz009"
down_revision: str | Sequence[str] | None = "crs002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLES = (
    "quiz_question",
    "quiz_question_revision",
    "quiz_practice_session_question",
    "quiz_exam_question",
)


def _json_type():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return postgresql.JSONB(astext_type=sa.Text())
    return sa.JSON()


def upgrade() -> None:
    json_type = _json_type()
    for table in TABLES:
        op.add_column(
            table,
            sa.Column("option_image_urls", json_type, nullable=True),
        )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_column(table, "option_image_urls")
