"""expand plan status length

Revision ID: plan003
Revises: quiz002
Create Date: 2026-08-12 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "plan003"
down_revision: str | Sequence[str] | None = "quiz002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ``registration_closed`` contains 19 characters.  PostgreSQL enforces
    # VARCHAR lengths by characters, so the historical VARCHAR(16) definition
    # could never store one of the values allowed by ck_plan_status.
    op.alter_column(
        "plan",
        "status",
        existing_type=sa.String(length=16),
        type_=sa.String(length=32),
        existing_nullable=False,
        existing_server_default="draft",
    )


def downgrade() -> None:
    op.alter_column(
        "plan",
        "status",
        existing_type=sa.String(length=32),
        type_=sa.String(length=16),
        existing_nullable=False,
        existing_server_default="draft",
    )
