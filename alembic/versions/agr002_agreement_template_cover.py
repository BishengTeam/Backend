"""add per-version agreement cover URL

Revision ID: agr002
Revises: agr001
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "agr002"
down_revision: str | Sequence[str] | None = "agr001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agreement_template",
        sa.Column("cover_url", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agreement_template", "cover_url")
