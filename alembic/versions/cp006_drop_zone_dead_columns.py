"""drop zone dead columns (is_banner/time window/link_url)

Revision ID: cp006_drop_zone_dead_columns
Revises: cp005_drop_banner_targets
"""
from alembic import op
from collections.abc import Sequence

import sqlalchemy as sa


revision: str = 'cp006_drop_zone_dead_columns'
down_revision: str | Sequence[str] = 'cp005_drop_banner_targets'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column('zone', 'is_banner')
    op.drop_column('zone', 'start_time')
    op.drop_column('zone', 'end_time')
    op.drop_column('zone', 'link_url')


def downgrade() -> None:
    op.add_column('zone', sa.Column('link_url', sa.String(length=512), nullable=True))
    op.add_column('zone', sa.Column('end_time', sa.DateTime(timezone=True), nullable=True))
    op.add_column('zone', sa.Column('start_time', sa.DateTime(timezone=True), nullable=True))
    op.add_column('zone', sa.Column('is_banner', sa.Boolean(), server_default='false', nullable=False))
