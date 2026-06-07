"""create_training_table

Revision ID: t001_create_training
Revises: m001_merge_heads
Create Date: 2026-06-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 't001_create_training'
down_revision: Union[str, Sequence[str], None] = 'm001_merge_heads'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if 'training' not in inspector.get_table_names():
        op.create_table('training',
            sa.Column('title', sa.String(256), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('cover_url', sa.String(512), nullable=True),
            sa.Column('location', sa.String(256), nullable=True),
            sa.Column('start_time', sa.DateTime(timezone=True), nullable=True),
            sa.Column('end_time', sa.DateTime(timezone=True), nullable=True),
            sa.Column('max_participants', sa.Integer(), server_default='0', nullable=False),
            sa.Column('cert_type', sa.String(64), nullable=True, comment='关联认证类型'),
            sa.Column('price', sa.Integer(), server_default='0', nullable=False, comment='培训费用（分）'),
            sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.PrimaryKeyConstraint('id')
        )


def downgrade() -> None:
    op.drop_table('training')
