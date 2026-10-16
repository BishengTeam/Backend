"""Versioned graduated-degree admission, preserving all legacy snapshots.

Revision ID: rsh004_graduated_degree
Revises: cp017_share_poster
"""

from alembic import op
import sqlalchemy as sa

revision = "rsh004_graduated_degree"
down_revision = "cp017_share_poster"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "renshe_application",
        sa.Column("correction_due_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "renshe_review",
        sa.Column("correction_due_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("renshe_review", sa.Column("checklist", sa.JSON(), nullable=True))
    op.drop_constraint("ck_renshe_material_kind", "renshe_material", type_="check")
    op.create_check_constraint(
        "ck_renshe_material_kind",
        "renshe_material",
        "kind IN ('id_card_front', 'id_card_back', 'portrait', 'student_card', 'xuexin_registration', 'education_proof', 'registration_portrait', 'graduation_certificate', 'education_report', 'commitment_letter')",
    )


def downgrade():
    # New data cannot be represented by the old material constraint. Refuse a
    # destructive downgrade rather than deleting submitted documents.
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT 1 FROM renshe_application WHERE draft_data -> 'education_info' IS NOT NULL LIMIT 1")).scalar():
        raise RuntimeError("已有学历型报名草稿，请关闭新入口并使用兼容版本回滚")
    if connection.execute(
        sa.text(
            "SELECT 1 FROM renshe_material WHERE kind IN ('registration_portrait', 'graduation_certificate', 'education_report', 'commitment_letter') LIMIT 1"
        )
    ).scalar():
        raise RuntimeError("已有学历型报名材料，请关闭新批次并使用兼容版本回滚")
    op.drop_constraint("ck_renshe_material_kind", "renshe_material", type_="check")
    op.create_check_constraint(
        "ck_renshe_material_kind",
        "renshe_material",
        "kind IN ('id_card_front', 'id_card_back', 'portrait', 'student_card', 'xuexin_registration', 'education_proof')",
    )
    op.drop_column("renshe_review", "checklist")
    op.drop_column("renshe_review", "correction_due_at")
    op.drop_column("renshe_application", "correction_due_at")
