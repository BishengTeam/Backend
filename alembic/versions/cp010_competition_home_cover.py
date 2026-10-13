"""competition home cover url

Revision ID: cp010_home_cover
Revises: r50001
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'cp010_home_cover'
down_revision: str | Sequence[str] = 'r50001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'competition',
        sa.Column('home_cover_url', sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('competition', 'home_cover_url')
