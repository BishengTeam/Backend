"""Merge certification migration heads

Revision ID: cp014_merge_cert_heads
Revises: cp013_cert_home_cover, cp013_cert_consultation_groups
Create Date: 2026-10-15
"""
from collections.abc import Sequence

from alembic import op


revision: str = 'cp014_merge_cert_heads'
down_revision: str | Sequence[str] = (
    'cp013_cert_home_cover',
    'cp013_cert_consultation_groups',
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Both parent migrations are independent and need no merge-time DDL."""


def downgrade() -> None:
    """Alembic downgrade stops before the branch merge point."""
