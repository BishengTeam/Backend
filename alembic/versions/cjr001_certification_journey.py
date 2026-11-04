"""Shared certification notices and consultation contacts."""
from alembic import op
import sqlalchemy as sa

revision = "cjr001"
down_revision = "crs006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("certification_journey_content",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope", sa.String(64), nullable=False, unique=True),
        sa.Column("vendor", sa.String(16), nullable=False),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("plan.id", ondelete="CASCADE")),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_certification_journey_content_vendor", "certification_journey_content", ["vendor"])


def downgrade():
    op.drop_table("certification_journey_content")
