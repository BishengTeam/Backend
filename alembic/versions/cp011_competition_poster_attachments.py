"""competition poster url and viewable attachments

同时合并当前双 head（cp010_home_cover 与 r60001）。

Revision ID: cp011_poster_attachments
Revises: cp010_home_cover, r60001
Create Date: 2026-10-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = 'cp011_poster_attachments'
down_revision: Union[str, Sequence[str], None] = (
    'cp010_home_cover',
    'r60001',
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('competition', sa.Column('poster_url', sa.String(length=512), nullable=True))
    op.add_column(
        'competition',
        sa.Column('attachments', JSONB(), nullable=True, server_default='[]'),
    )


def downgrade() -> None:
    op.drop_column('competition', 'attachments')
    op.drop_column('competition', 'poster_url')
