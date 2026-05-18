"""harden order constraints

Revision ID: b1c2d3e4f5a6
Revises: a0b1c2d3e4f5
Create Date: 2026-05-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, Sequence[str], None] = 'a0b1c2d3e4f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_order_status",
        "order",
        "status IN ('pending', 'paid', 'completed', 'refunded')",
    )
    op.alter_column(
        "order",
        "paid_at",
        existing_type=sa.String(length=30),
        type_=sa.DateTime(timezone=True),
        existing_nullable=True,
        postgresql_using="NULLIF(paid_at, '')::timestamptz",
    )
    op.create_index(
        "uq_price_config_active_cert_user",
        "price_config",
        ["cert_type", "user_type"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )


def downgrade() -> None:
    op.drop_index("uq_price_config_active_cert_user", table_name="price_config")
    op.alter_column(
        "order",
        "paid_at",
        existing_type=sa.DateTime(timezone=True),
        type_=sa.String(length=30),
        existing_nullable=True,
        postgresql_using="paid_at::text",
    )
    op.drop_constraint("ck_order_status", "order", type_="check")
