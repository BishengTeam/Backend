"""create plan table

Revision ID: p002_plan_table
Revises: dc4bbbaac009
Create Date: 2026-07-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "p002_plan_table"
down_revision: Union[str, Sequence[str], None] = "dc4bbbaac009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "plan",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("product_type", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("apply_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("apply_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exam_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("capacity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('draft','published','archived','cancelled')",
            name="ck_plan_status",
        ),
        sa.UniqueConstraint("product_type", "name", name="uq_plan_product_name"),
    )
    op.create_index("ix_plan_product_type", "plan", ["product_type"])
    op.create_index("ix_plan_status", "plan", ["status"])


def downgrade() -> None:
    op.drop_index("ix_plan_status", table_name="plan")
    op.drop_index("ix_plan_product_type", table_name="plan")
    op.drop_table("plan")
