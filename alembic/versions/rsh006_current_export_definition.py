"""Identify in-flight exports without rewriting existing artifacts.

Revision ID: rsh006_current_export
Revises: rsh005_standard_materials
"""

from alembic import op
import sqlalchemy as sa

revision = "rsh006_current_export"
down_revision = "rsh005_standard_materials"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "renshe_export_job",
        sa.Column("definition_fingerprint", sa.String(64), nullable=True),
    )


def downgrade():
    op.drop_column("renshe_export_job", "definition_fingerprint")
