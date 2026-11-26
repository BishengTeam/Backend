"""videoweb access switch: per-user disable flag

Revision ID: vweb002_user_access
Revises: vweb001_course_code

B6 学习授权页需要"禁用/启用视频站访问"，用可逆标记列而不是批量撤销授权：
禁用只影响视频站判权，课程授权数据保持原样，启用后立即恢复。
"""
from alembic import op
import sqlalchemy as sa

revision = "vweb002_user_access"
down_revision = "vweb001_course_code"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("videoweb_access_disabled_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user", "videoweb_access_disabled_at")
