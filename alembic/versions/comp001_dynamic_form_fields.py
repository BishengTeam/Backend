"""competition dynamic form fields

Revision ID: comp001
Revises: nisp003
Create Date: 2026-10-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = 'comp001'
down_revision: Union[str, Sequence[str], None] = 'nisp003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('competition', sa.Column('custom_fields', JSONB(), nullable=True, server_default='[]'))
    op.add_column('competition_reg', sa.Column('custom_field_values', JSONB(), nullable=True, server_default='{}'))


def downgrade() -> None:
    op.drop_column('competition_reg', 'custom_field_values')
    op.drop_column('competition', 'custom_fields')
