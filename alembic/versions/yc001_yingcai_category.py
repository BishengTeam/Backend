"""yingcai plan category on activity (employment zone replaced by yingcai zone)

就业专区关闭、原位新增英才计划专区：activity 复用为英才计划活动载体，
加 category 列区分普通活动（normal）与英才计划活动（yingcai）。
存量行回填 normal，公开列表与管理端列表默认排除 yingcai，两类专区互不可见。

Revision ID: yc001_yingcai_category
Revises: rb001_recycle_bin
"""
from alembic import op
from collections.abc import Sequence

import sqlalchemy as sa


revision: str = 'yc001_yingcai_category'
down_revision: str | Sequence[str] = 'rb001_recycle_bin'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'activity',
        sa.Column('category', sa.String(length=16), nullable=True, server_default='normal'),
    )
    op.execute("UPDATE activity SET category = 'normal' WHERE category IS NULL")


def downgrade() -> None:
    op.drop_column('activity', 'category')
