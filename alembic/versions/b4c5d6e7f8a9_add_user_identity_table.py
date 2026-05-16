"""add user_identity table

Revision ID: b4c5d6e7f8a9
Revises: a3b2c1d4e5f6
Create Date: 2026-05-17 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b4c5d6e7f8a9'
down_revision: Union[str, Sequence[str], None] = 'a3b2c1d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('user_identity',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('real_name', sa.String(length=64), nullable=False),
    sa.Column('id_card_number', sa.String(length=18), nullable=False),
    sa.Column('id_card_front_oss', sa.String(length=512), nullable=True),
    sa.Column('id_card_back_oss', sa.String(length=512), nullable=True),
    sa.Column('student_card_oss', sa.String(length=512), nullable=True),
    sa.Column('status', sa.String(length=16), server_default='pending', nullable=False),
    sa.Column('verified_at', sa.String(length=30), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id'),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'])
    )
    op.create_index(op.f('ix_user_identity_id_card_number'), 'user_identity', ['id_card_number'], unique=False)
    op.create_index(op.f('ix_user_identity_status'), 'user_identity', ['status'], unique=False)
    op.create_index(op.f('ix_user_identity_user_id'), 'user_identity', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_table('user_identity')
