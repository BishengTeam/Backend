"""Standard Renshe submission package materials.

Revision ID: rsh005_standard_materials
Revises: rsh004_graduated_degree
"""

from alembic import op
import sqlalchemy as sa

revision = "rsh005_standard_materials"
down_revision = "rsh004_graduated_degree"
branch_labels = None
depends_on = None

NEW_KINDS = (
    "social_security_proof",
    "vocational_qualification_certificate",
    "professional_title_certificate",
)
KINDS = (
    "id_card_front",
    "id_card_back",
    "portrait",
    "student_card",
    "xuexin_registration",
    "education_proof",
    "registration_portrait",
    "graduation_certificate",
    "education_report",
    "commitment_letter",
    *NEW_KINDS,
)


def upgrade():
    op.drop_constraint("ck_renshe_material_kind", "renshe_material", type_="check")
    op.create_check_constraint(
        "ck_renshe_material_kind",
        "renshe_material",
        "kind IN (" + ", ".join(repr(kind) for kind in KINDS) + ")",
    )


def downgrade():
    connection = op.get_bind()
    if connection.execute(
        sa.text(
            "SELECT 1 FROM renshe_material WHERE kind IN ("
            + ", ".join(repr(kind) for kind in NEW_KINDS)
            + ") LIMIT 1"
        )
    ).scalar():
        raise RuntimeError("已有标准报送材料，请使用兼容版本回滚")
    op.drop_constraint("ck_renshe_material_kind", "renshe_material", type_="check")
    op.create_check_constraint(
        "ck_renshe_material_kind",
        "renshe_material",
        "kind IN (" + ", ".join(repr(kind) for kind in KINDS[:-3]) + ")",
    )
