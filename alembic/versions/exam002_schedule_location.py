"""Add exam location to H3C exam schedules.

Revision ID: exam002
Revises: u10_order_unique
"""
from alembic import op
import sqlalchemy as sa


revision = "exam002"
down_revision = "u10_order_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "exam_schedule",
        sa.Column("exam_location", sa.String(length=256), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("exam_schedule", "exam_location")
