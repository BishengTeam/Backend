"""make job.company optional

Revision ID: job001
Revises: quiz016
Create Date: 2026-10-21 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "job001"
down_revision: str | Sequence[str] | None = "quiz016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "job",
        "company",
        existing_type=sa.String(length=128),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "job",
        "company",
        existing_type=sa.String(length=128),
        nullable=False,
    )
