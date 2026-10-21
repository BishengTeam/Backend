"""gzh_articles table for home news ticker (公众号资讯栏)

Revision ID: cp017_create_gzh_articles
Revises: job001
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'cp017_create_gzh_articles'
down_revision: str | Sequence[str] = 'job001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'gzh_articles',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('title', sa.String(length=256), nullable=False),
        sa.Column('url', sa.String(length=512), nullable=False),
        sa.Column('thumb_url', sa.String(length=512), nullable=True),
        sa.Column('publish_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('url', name='uq_gzh_articles_url'),
    )


def downgrade() -> None:
    op.drop_table('gzh_articles')
