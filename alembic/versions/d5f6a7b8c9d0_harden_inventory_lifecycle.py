"""harden inventory lifecycle

Revision ID: d5f6a7b8c9d0
Revises: c4e5f6a7b8c9
Create Date: 2026-07-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "c4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_order_active_user_plan",
        "order",
        ["user_id", "plan_id"],
        unique=True,
        postgresql_where=sa.text(
            "plan_id IS NOT NULL AND status IN ('pending', 'paid', 'completed')"
        ),
    )
    op.create_index(
        "uq_inventory_record_order_action",
        "inventory_record",
        ["order_id", "action"],
        unique=True,
        postgresql_where=sa.text("order_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_inventory_record_order_action",
        table_name="inventory_record",
    )
    op.drop_index("uq_order_active_user_plan", table_name="order")
