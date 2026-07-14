"""add plan id to order

Revision ID: c4e5f6a7b8c9
Revises: 8e233e41d4bf, a001_course_chapter
Create Date: 2026-07-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = (
    "8e233e41d4bf",
    "a001_course_chapter",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("order", sa.Column("plan_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_order_plan_id_plan",
        "order",
        "plan",
        ["plan_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_order_plan_id", "order", ["plan_id"])


def downgrade() -> None:
    op.drop_index("ix_order_plan_id", table_name="order")
    op.drop_constraint("fk_order_plan_id_plan", "order", type_="foreignkey")
    op.drop_column("order", "plan_id")
