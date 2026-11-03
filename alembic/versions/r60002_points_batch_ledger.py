"""points batch ledger: expiry batches, FIFO consumption, negative balance on clawback

- 新建 points_batch 批次账本（每笔获得一个批次，先进先出消费，到期清零）
- 放开 user_points.balance / points_history.balance_after 的非负约束：
  退款追扣缴费返积分时余额不足将扣至负数（欠分不过期，兑换被余额校验拦截）
- 存量余额整体并入一个继承批次，有效期自本次发布起算 12 个月（365 天）

Revision ID: r60002
Revises: quiz018
Create Date: 2026-11-03 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r60002"
down_revision: str | Sequence[str] | None = "quiz018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "points_batch",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("remaining", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action_type", sa.String(length=32), nullable=False),
        sa.Column("description", sa.String(length=256), nullable=True),
        sa.Column("source_type", sa.String(length=32), nullable=True),
        sa.Column("source_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.CheckConstraint("amount > 0", name="ck_points_batch_amount_positive"),
        sa.CheckConstraint("remaining >= 0", name="ck_points_batch_remaining_non_negative"),
        sa.CheckConstraint("remaining <= amount", name="ck_points_batch_remaining_not_exceed_amount"),
    )
    op.create_index("ix_points_batch_user_id", "points_batch", ["user_id"])
    op.create_index("ix_points_batch_expires_at", "points_batch", ["expires_at"])
    op.create_index(
        "ix_points_batch_fifo",
        "points_batch",
        ["user_id", "expires_at"],
        postgresql_where=sa.text("remaining > 0"),
    )
    op.drop_constraint("ck_user_points_balance_non_negative", "user_points", type_="check")
    op.drop_constraint("ck_points_history_balance_after_non_negative", "points_history", type_="check")
    # 存量余额并入一个继承批次：不逐笔回溯（早期流水含已废弃旧规则），
    # 统一自发布日起算 12 个月过期，客服口径「老积分统一于 <发布日+1年> 到期」。
    op.execute(
        """
        INSERT INTO points_batch
            (user_id, amount, remaining, expires_at, action_type, description,
             created_at, updated_at)
        SELECT user_id, balance, balance, now() + INTERVAL '365 days',
               'legacy_migration', '存量积分迁移（有效期12个月）', now(), now()
        FROM user_points
        WHERE balance > 0
        """
    )


def downgrade() -> None:
    op.drop_index("ix_points_batch_fifo", table_name="points_batch")
    op.drop_index("ix_points_batch_expires_at", table_name="points_batch")
    op.drop_index("ix_points_batch_user_id", table_name="points_batch")
    op.drop_table("points_batch")
    # 若追扣已产生负余额，恢复非负约束会失败——需先人工清账再降级。
    op.create_check_constraint(
        "ck_points_history_balance_after_non_negative",
        "points_history",
        "balance_after >= 0",
    )
    op.create_check_constraint(
        "ck_user_points_balance_non_negative",
        "user_points",
        "balance >= 0",
    )
