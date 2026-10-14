"""Shared certification batch consultation groups

Revision ID: cp013_cert_consultation_groups
Revises: rsh002_review_export_binding
Create Date: 2026-10-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "cp013_cert_consultation_groups"
down_revision: Union[str, Sequence[str], None] = "rsh002_review_export_binding"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cert_consultation_group",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("qrcode_url", sa.String(length=512), nullable=False),
        sa.Column("join_instructions", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["plan_id"], ["plan.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", name="uq_cert_consultation_group_plan"),
    )
    op.execute(
        """
        INSERT INTO cert_consultation_group
            (created_at, updated_at, plan_id, qrcode_url, join_instructions,
             expires_at, version, updated_by)
        SELECT now(), now(), b.plan_id, b.consultation_group_qrcode_url,
               NULL, NULL, 1, NULL
        FROM nisp_exam_batch b
        WHERE b.consultation_group_qrcode_url IS NOT NULL
          AND b.consultation_group_qrcode_url <> ''
        ON CONFLICT (plan_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("cert_consultation_group")
