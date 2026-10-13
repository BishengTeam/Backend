"""NISP batch consultation group QR code

Revision ID: cp012_nisp_consultation_qrcode
Revises: cp011_poster_attachments
Create Date: 2026-10-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "cp012_nisp_consultation_qrcode"
down_revision: Union[str, Sequence[str], None] = "cp011_poster_attachments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "nisp_exam_batch",
        sa.Column("consultation_group_qrcode_url", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("nisp_exam_batch", "consultation_group_qrcode_url")
