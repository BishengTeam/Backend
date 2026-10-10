"""Automatic refunds for payments received after order closure

Revision ID: pay001
Revises: nisp004
Create Date: 2026-10-10
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "pay001"
down_revision: Union[str, Sequence[str] | None] = "nisp004"
branch_labels: Union[str, Sequence[str] | None] = None
depends_on: Union[str, Sequence[str] | None] = None


def upgrade() -> None:
    op.create_table(
        "payment_refund_task",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("order.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("out_trade_no", sa.String(length=64), nullable=False),
        sa.Column("transaction_id", sa.String(length=64), nullable=False),
        sa.Column("out_refund_no", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=128), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("submit_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processing_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("manual_takeover_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("wechat_refund_id", sa.String(length=64), nullable=True),
        sa.Column("last_provider_status", sa.String(length=32), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("out_refund_no", name="uq_payment_refund_task_refund_no"),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'succeeded', 'failed', 'manual_review')",
            name="ck_payment_refund_task_status",
        ),
        sa.CheckConstraint(
            "amount_cents > 0", name="ck_payment_refund_task_amount"
        ),
        sa.CheckConstraint("retry_count >= 0", name="ck_payment_refund_task_retry"),
        sa.CheckConstraint(
            "length(out_refund_no) > 0 AND length(out_refund_no) <= 32",
            name="ck_payment_refund_task_refund_no",
        ),
    )
    op.create_index(
        "ix_payment_refund_task_order_id",
        "payment_refund_task",
        ["order_id"],
        unique=True,
    )
    op.create_index("ix_payment_refund_task_user_id", "payment_refund_task", ["user_id"])
    op.create_index(
        "ix_payment_refund_task_due",
        "payment_refund_task",
        ["status", "next_attempt_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_payment_refund_task_due", table_name="payment_refund_task")
    op.drop_index("ix_payment_refund_task_user_id", table_name="payment_refund_task")
    op.drop_index(
        "ix_payment_refund_task_order_id", table_name="payment_refund_task"
    )
    op.drop_table("payment_refund_task")
