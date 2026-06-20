"""create_review_table

Revision ID: r001_create_review
Revises: o001_order_refactor
Create Date: 2026-06-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'r001_create_review'
down_revision: Union[str, Sequence[str], None] = 'o001_order_refactor'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table("review",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", sa.Integer, nullable=False),
        sa.Column("reviewer_id", sa.Integer,
                  sa.ForeignKey("admin_user.id"), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_review_target_type", "review", ["target_type"])
    op.create_index("ix_review_target_id", "review", ["target_id"])
    op.create_index("ix_review_reviewer_id", "review", ["reviewer_id"])


def downgrade() -> None:
    op.drop_table("review")
