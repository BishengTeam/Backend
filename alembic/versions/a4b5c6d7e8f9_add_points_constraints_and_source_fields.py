"""add points constraints and source fields

Revision ID: a4b5c6d7e8f9
Revises: f2a3b4c5d6e7
Create Date: 2026-05-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a4b5c6d7e8f9"
down_revision: Union[str, Sequence[str], None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("points_history", sa.Column("source_type", sa.String(length=32), nullable=True))
    op.add_column("points_history", sa.Column("source_id", sa.String(length=64), nullable=True))
    op.create_check_constraint(
        "ck_user_points_balance_non_negative",
        "user_points",
        "balance >= 0",
    )
    op.create_check_constraint(
        "ck_points_history_amount_non_zero",
        "points_history",
        "amount <> 0",
    )
    op.create_check_constraint(
        "ck_points_history_balance_after_non_negative",
        "points_history",
        "balance_after >= 0",
    )
    op.create_index(
        "uq_points_history_user_source_action",
        "points_history",
        ["user_id", "source_type", "source_id", "action_type"],
        unique=True,
        postgresql_where=sa.text("source_type IS NOT NULL AND source_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_points_history_user_source_action", table_name="points_history")
    op.drop_constraint(
        "ck_points_history_balance_after_non_negative",
        "points_history",
        type_="check",
    )
    op.drop_constraint("ck_points_history_amount_non_zero", "points_history", type_="check")
    op.drop_constraint("ck_user_points_balance_non_negative", "user_points", type_="check")
    op.drop_column("points_history", "source_id")
    op.drop_column("points_history", "source_type")
