"""add edit_count to user_identity, change status default to verified

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-05-17 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd6e7f8a9b0c1'
down_revision: Union[str, Sequence[str], None] = 'c5d6e7f8a9b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('user_identity', sa.Column('edit_count', sa.Integer(), server_default='0', nullable=False))
    op.alter_column('user_identity', 'status', server_default='verified')


def downgrade() -> None:
    op.drop_column('user_identity', 'edit_count')
    op.alter_column('user_identity', 'status', server_default='pending')
