"""Bind Renshe external reviews to exported snapshots

Revision ID: rsh002_review_export_binding
Revises: cp012_nisp_consultation_qrcode
Create Date: 2026-10-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "rsh002_review_export_binding"
down_revision: Union[str, Sequence[str], None] = "cp012_nisp_consultation_qrcode"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "renshe_review",
        sa.Column("export_item_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_renshe_review_export_item",
        "renshe_review",
        ["export_item_id"],
    )
    op.create_foreign_key(
        "fk_renshe_review_export_item",
        "renshe_review",
        "renshe_export_item",
        ["export_item_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_renshe_review_export_item",
        "renshe_review",
        type_="foreignkey",
    )
    op.drop_index("ix_renshe_review_export_item", table_name="renshe_review")
    op.drop_column("renshe_review", "export_item_id")
