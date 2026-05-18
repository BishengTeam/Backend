"""add closed order status and inventory fields

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-05-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d3e4f5a6b7c8"
down_revision: Union[str, Sequence[str], None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_order_status", "order", type_="check")
    op.add_column("order", sa.Column("inventory_id", sa.Integer(), nullable=True))
    op.add_column("order", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("order", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("order", sa.Column("close_reason", sa.String(length=128), nullable=True))
    op.create_index("ix_order_inventory_id", "order", ["inventory_id"], unique=False)
    op.create_check_constraint(
        "ck_order_status",
        "order",
        "status IN ('pending', 'paid', 'completed', 'refunded', 'closed')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_order_status", "order", type_="check")
    op.drop_index("ix_order_inventory_id", table_name="order")
    op.drop_column("order", "close_reason")
    op.drop_column("order", "closed_at")
    op.drop_column("order", "expires_at")
    op.drop_column("order", "inventory_id")
    op.create_check_constraint(
        "ck_order_status",
        "order",
        "status IN ('pending', 'paid', 'completed', 'refunded')",
    )
