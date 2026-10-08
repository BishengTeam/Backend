"""create generic operational document resource

Revision ID: doc001
Revises: comp001
Create Date: 2026-10-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "doc001"
down_revision: Union[str, Sequence[str], None] = "comp001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_resource",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("document_key", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("original_filename", sa.String(length=256), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("version_no", sa.Integer(), server_default="1", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["admin_user.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["admin_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("version_no > 0", name="ck_document_resource_version_positive"),
        sa.CheckConstraint("length(trim(title)) > 0", name="ck_document_resource_title_non_empty"),
        sa.UniqueConstraint("document_key", name="uq_document_resource_key"),
    )
    op.create_index(
        "ix_document_resource_is_active", "document_resource", ["is_active"]
    )


def downgrade() -> None:
    op.drop_index("ix_document_resource_is_active", table_name="document_resource")
    op.drop_table("document_resource")
