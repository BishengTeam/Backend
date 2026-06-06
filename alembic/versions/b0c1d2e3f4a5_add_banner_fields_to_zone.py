"""add_banner_fields_to_zone

Revision ID: b0c1d2e3f4a5
Revises: merge_heads_001
Create Date: 2026-06-06

将 Banner 功能合并到 Zone：新增 is_banner / start_time / end_time 字段
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b0c1d2e3f4a5'
down_revision: Union[str, None] = 'merge_heads_001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('zone', sa.Column('is_banner', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('zone', sa.Column('start_time', sa.DateTime(timezone=True), nullable=True))
    op.add_column('zone', sa.Column('end_time', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_zone_is_banner'), 'zone', ['is_banner'])


def downgrade() -> None:
    op.drop_index(op.f('ix_zone_is_banner'), table_name='zone')
    op.drop_column('zone', 'end_time')
    op.drop_column('zone', 'start_time')
    op.drop_column('zone', 'is_banner')
