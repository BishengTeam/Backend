"""simplify user table: drop user_type, age_group, certification_interest, profile_edit_count

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-05-17 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c5d6e7f8a9b0'
down_revision: Union[str, Sequence[str], None] = 'b4c5d6e7f8a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('user', 'profile_edit_count')
    op.drop_column('user', 'certification_interest')
    op.drop_column('user', 'age_group')
    op.drop_column('user', 'user_type')


def downgrade() -> None:
    op.add_column('user', sa.Column('user_type', sa.String(length=16), server_default='student', nullable=False))
    op.add_column('user', sa.Column('age_group', sa.String(length=16), nullable=True))
    op.add_column('user', sa.Column('certification_interest', sa.String(length=64), nullable=True))
    op.add_column('user', sa.Column('profile_edit_count', sa.Integer(), server_default='0', nullable=False))
