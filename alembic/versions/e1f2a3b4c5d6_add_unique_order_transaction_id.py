"""add unique order transaction id

Revision ID: e1f2a3b4c5d6
Revises: d3e4f5a6b7c8
Create Date: 2026-05-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "d3e4f5a6b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_order_transaction_id_unique",
        "order",
        ["transaction_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_order_transaction_id_unique", table_name="order")
