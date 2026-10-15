"""share poster table for mini-program share page

Revision ID: cp017_share_poster
Revises: cp016_drop_comp_home_cover
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'cp017_share_poster'
down_revision: str | Sequence[str] = 'cp016_drop_comp_home_cover'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'share_poster',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('image_url', sa.String(length=512), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('share_poster')
