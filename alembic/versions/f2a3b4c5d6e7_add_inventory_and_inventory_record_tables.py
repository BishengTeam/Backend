"""add inventory and inventory_record tables

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-05-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, Sequence[str], None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "inventory",
        sa.Column("inventory_type", sa.String(length=32), nullable=False, server_default="certification"),
        sa.Column("ref_code", sa.String(length=64), nullable=False),
        sa.Column("total_quota", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_quota", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_quota", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sold_quota", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("total_quota >= 0", name="ck_inventory_total_quota_non_negative"),
        sa.CheckConstraint("available_quota >= 0", name="ck_inventory_available_quota_non_negative"),
        sa.CheckConstraint("locked_quota >= 0", name="ck_inventory_locked_quota_non_negative"),
        sa.CheckConstraint("sold_quota >= 0", name="ck_inventory_sold_quota_non_negative"),
        sa.CheckConstraint(
            "available_quota + locked_quota + sold_quota = total_quota",
            name="ck_inventory_quota_balance",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("inventory_type", "ref_code", name="uq_inventory_type_ref_code"),
    )
    op.create_index("ix_inventory_inventory_type", "inventory", ["inventory_type"], unique=False)
    op.create_foreign_key(
        "fk_order_inventory_id_inventory",
        "order",
        "inventory",
        ["inventory_id"],
        ["id"],
    )

    op.create_table(
        "inventory_record",
        sa.Column("inventory_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("before_total_quota", sa.Integer(), nullable=False),
        sa.Column("before_available_quota", sa.Integer(), nullable=False),
        sa.Column("before_locked_quota", sa.Integer(), nullable=False),
        sa.Column("before_sold_quota", sa.Integer(), nullable=False),
        sa.Column("after_total_quota", sa.Integer(), nullable=False),
        sa.Column("after_available_quota", sa.Integer(), nullable=False),
        sa.Column("after_locked_quota", sa.Integer(), nullable=False),
        sa.Column("after_sold_quota", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=256), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["inventory_id"], ["inventory.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["order.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inventory_record_inventory_id", "inventory_record", ["inventory_id"], unique=False)
    op.create_index("ix_inventory_record_order_id", "inventory_record", ["order_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_inventory_record_order_id", table_name="inventory_record")
    op.drop_index("ix_inventory_record_inventory_id", table_name="inventory_record")
    op.drop_table("inventory_record")
    op.drop_constraint("fk_order_inventory_id_inventory", "order", type_="foreignkey")
    op.drop_index("ix_inventory_inventory_type", table_name="inventory")
    op.drop_table("inventory")
