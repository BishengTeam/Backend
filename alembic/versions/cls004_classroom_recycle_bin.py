"""classroom soft delete: 课堂进回收站

Revision ID: cls004_classroom_recycle_bin
Revises: adm004
"""
from alembic import op
import sqlalchemy as sa

revision = "cls004_classroom_recycle_bin"
down_revision = "adm004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("classroom", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.add_column("classroom", sa.Column("restore_until", sa.DateTime(timezone=True)))
    # 部分索引：管理端列表与学生端「我的课堂」恒带 deleted_at IS NULL 过滤
    op.create_index(
        "ix_classroom_deleted_at", "classroom", ["deleted_at"],
        postgresql_where=sa.text("deleted_at IS NULL"),
        sqlite_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_classroom_deleted_at", table_name="classroom")
    op.drop_column("classroom", "restore_until")
    op.drop_column("classroom", "deleted_at")
