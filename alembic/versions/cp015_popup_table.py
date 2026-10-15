"""popup table for mini-program poster modal

Revision ID: cp015_popup_table
Revises: rsh003_project_versions
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'cp015_popup_table'
down_revision: str | Sequence[str] = 'rsh003_project_versions'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'popup',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('image_url', sa.String(length=512), nullable=False),
        sa.Column('jump_link', sa.String(length=512), nullable=True),
        sa.Column('sort', sa.Integer(), server_default='0', nullable=False),
        sa.Column('start_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('end_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('popup')
