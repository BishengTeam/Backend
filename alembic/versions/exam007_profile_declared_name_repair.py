"""Repair user_profile.declared_name for databases that skipped u11.

Revision ID: exam007
Revises: exam006
"""
from alembic import op


revision = "exam007"
down_revision = "exam006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 迁移图线性化前已升级到 exam004+ 的数据库不会回头执行 u11，
    # 这里做一次幂等补偿；全新库上 u11 已建列，此处为 no-op。
    op.execute(
        "ALTER TABLE user_profile "
        "ADD COLUMN IF NOT EXISTS declared_name VARCHAR(32)"
    )


def downgrade() -> None:
    # 列由 u11 负责删除；保留该版本不做破坏性回滚。
    pass
