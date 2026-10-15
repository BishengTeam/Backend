"""drop competition home cover url（首页竖版封面功能下线，首页统一用横版 cover_url）

Revision ID: cp016_drop_competition_home_cover
Revises: cp015_popup_table
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'cp016_drop_competition_home_cover'
down_revision: str | Sequence[str] = 'cp015_popup_table'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column('competition', 'home_cover_url')


def downgrade() -> None:
    op.add_column(
        'competition',
        sa.Column('home_cover_url', sa.String(length=512), nullable=True),
    )
