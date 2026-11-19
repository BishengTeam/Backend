"""Add generic certification attachments (shared by all vendors).

Revision ID: exam005
Revises: exam004
"""
from alembic import op
import sqlalchemy as sa


revision = "exam005"
down_revision = "exam004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cert_attachment",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("vendor", sa.String(length=32), nullable=False),
        sa.Column(
            "schedule_id",
            sa.Integer(),
            sa.ForeignKey("exam_schedule.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("file_url", sa.String(length=512), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_cert_attachment_vendor",
        "cert_attachment",
        ["vendor", "schedule_id", "is_active"],
    )


def downgrade() -> None:
    op.drop_index("ix_cert_attachment_vendor", table_name="cert_attachment")
    op.drop_table("cert_attachment")
