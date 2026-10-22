"""Preserve the server-generated definition of each completed review."""
from alembic import op
import sqlalchemy as sa

revision = "rsh008_review_requirements"
down_revision = "pay002_offline"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("renshe_review", sa.Column("checklist_snapshot", sa.JSON(), nullable=True))


def downgrade():
    # Retain historical audit definitions when rolling the application back.
    raise RuntimeError("审核快照须留存；回滚应用时保留此迁移和数据列")
