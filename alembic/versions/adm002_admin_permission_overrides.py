"""Add per-admin permission overrides column.

覆盖式按人授权：NULL = 跟随角色预设；非 NULL 列表 = 该管理员的最终权限集。
角色 CHECK（ck_admin_user_role）与唯一超管索引不变。

Revision ID: adm002
Revises: exam003
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "adm002"
down_revision = "exam003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_context().as_sql:
        return
    op.add_column(
        "admin_user",
        sa.Column("permissions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    if op.get_context().as_sql:
        return
    op.drop_column("admin_user", "permissions")
