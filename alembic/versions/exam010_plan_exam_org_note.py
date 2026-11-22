"""Add exam organization/date note and renshe version batch snapshot.

Revision ID: exam010
Revises: exam009
"""
from alembic import op
import sqlalchemy as sa


revision = "exam010"
down_revision = "exam009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plan", sa.Column("exam_org_name", sa.String(length=128), nullable=True))
    op.add_column("plan", sa.Column("exam_org_code", sa.String(length=64), nullable=True))
    op.add_column("plan", sa.Column("exam_date_note", sa.String(length=64), nullable=True))
    op.add_column(
        "renshe_application_version",
        sa.Column("batch_snapshot", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("renshe_application_version", "batch_snapshot")
    op.drop_column("plan", "exam_date_note")
    op.drop_column("plan", "exam_org_code")
    op.drop_column("plan", "exam_org_name")
