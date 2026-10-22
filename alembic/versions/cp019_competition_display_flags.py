"""competition detail display flags（按赛事隐藏详情页比赛时间/赛道报名区块）

Revision ID: cp019_comp_display_flags
Revises: cp018_create_gzh_articles
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'cp019_comp_display_flags'
down_revision: str | Sequence[str] = 'cp018_create_gzh_articles'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'competition',
        sa.Column('show_schedule', sa.Boolean(), nullable=False, server_default=sa.text('true')),
    )
    op.add_column(
        'competition',
        sa.Column('show_signup', sa.Boolean(), nullable=False, server_default=sa.text('true')),
    )


def downgrade() -> None:
    op.drop_column('competition', 'show_signup')
    op.drop_column('competition', 'show_schedule')
