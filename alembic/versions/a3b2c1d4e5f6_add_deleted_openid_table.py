"""add deleted_openid table

Revision ID: a3b2c1d4e5f6
Revises: f9d64f386d8f
Create Date: 2026-05-16 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3b2c1d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'f9d64f386d8f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('deleted_openid',
    sa.Column('openid', sa.String(length=64), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('openid')
    )
    op.create_index(op.f('ix_deleted_openid_openid'), 'deleted_openid', ['openid'], unique=False)


def downgrade() -> None:
    op.drop_table('deleted_openid')
