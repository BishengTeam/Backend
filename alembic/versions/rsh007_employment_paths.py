"""Add the separately submitted work proof material.

Revision ID: rsh007_employment_paths
Revises: rsh006_current_export
"""
import sqlalchemy as sa

from alembic import op

revision = "rsh007_employment_paths"
down_revision = "rsh006_current_export"
branch_labels = None
depends_on = None

KINDS = (
    "id_card_front", "id_card_back", "portrait", "student_card", "xuexin_registration", "education_proof",
    "registration_portrait", "graduation_certificate", "education_report", "commitment_letter",
    "social_security_proof", "vocational_qualification_certificate", "professional_title_certificate",
)


def upgrade():
    op.drop_constraint("ck_renshe_material_kind", "renshe_material", type_="check")
    op.create_check_constraint("ck_renshe_material_kind", "renshe_material", "kind IN (" + ", ".join(repr(kind) for kind in (*KINDS, "work_proof")) + ")")


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM renshe_material WHERE kind = 'work_proof' LIMIT 1")).scalar():
        raise RuntimeError("已有工龄证明材料，请使用兼容版本回滚，不能删除材料类型")
    op.drop_constraint("ck_renshe_material_kind", "renshe_material", type_="check")
    op.create_check_constraint("ck_renshe_material_kind", "renshe_material", "kind IN (" + ", ".join(repr(kind) for kind in KINDS) + ")")
