"""Allow mixed full-package H3C exports.

Revision ID: r50001
Revises: r40001
Create Date: 2026-10-11
"""

from alembic import op


revision = "r50001"
down_revision = "r40001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_h3c_export_artifact_type", "h3c_export_job", type_="check"
    )
    op.create_check_constraint(
        "ck_h3c_export_artifact_type",
        "h3c_export_job",
        "artifact_type IN ('embedded_xlsx', 'images_zip', 'full_package')",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE h3c_export_job SET artifact_type = 'images_zip', "
        "last_error = COALESCE(last_error, 'full_package unsupported after downgrade') "
        "WHERE artifact_type = 'full_package'"
    )
    op.drop_constraint(
        "ck_h3c_export_artifact_type", "h3c_export_job", type_="check"
    )
    op.create_check_constraint(
        "ck_h3c_export_artifact_type",
        "h3c_export_job",
        "artifact_type IN ('embedded_xlsx', 'images_zip')",
    )
