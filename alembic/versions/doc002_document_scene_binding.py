"""bind operational documents to fixed mini-program scenes

Revision ID: doc002
Revises: doc001
Create Date: 2026-10-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "doc002"
down_revision: Union[str, Sequence[str], None] = "doc001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("document_resource", sa.Column("scene", sa.String(length=64), nullable=True))
    op.add_column("document_resource", sa.Column("entry_text", sa.String(length=64), nullable=True))
    op.add_column("document_resource", sa.Column("entry_mode", sa.String(length=16), nullable=True))
    op.execute(
        """
        UPDATE document_resource
        SET scene = 'h3c_student_xuexin_guide',
            entry_text = '查看《如何查询学籍在线验证码》PDF',
            entry_mode = 'required'
        WHERE document_key = 'h3c.xuexin_verification_guide'
        """
    )
    op.create_index(
        "uq_document_resource_scene",
        "document_resource",
        ["scene"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_document_resource_scene", table_name="document_resource")
    op.drop_column("document_resource", "entry_mode")
    op.drop_column("document_resource", "entry_text")
    op.drop_column("document_resource", "scene")
