"""remove price_enterprise, price_student from certification (moved to price_config)

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-05-17 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f8a9b0c1d2e3'
down_revision: Union[str, Sequence[str], None] = 'e7f8a9b0c1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('certification', 'price_student')
    op.drop_column('certification', 'price_enterprise')


def downgrade() -> None:
    op.add_column('certification', sa.Column('price_enterprise', sa.Integer(), server_default='0', nullable=False))
    op.add_column('certification', sa.Column('price_student', sa.Integer(), server_default='0', nullable=False))
