"""Add plan.registration_notice for registration-stage policy.

Revision ID: exam009
Revises: exam008
"""
from alembic import op
import sqlalchemy as sa


revision = "exam009"
down_revision = "exam008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "plan",
        sa.Column("registration_notice", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("plan", "registration_notice")
