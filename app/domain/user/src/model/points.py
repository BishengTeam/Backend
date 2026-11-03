from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class UserPoints(Base, TimestampMixin):
    __tablename__ = "user_points"
    # 余额允许为负：退款追扣缴费返积分时若余额不足，将扣至负数（欠分不过期）；
    # 兑换侧凭「balance < 所需积分」自然拦截，后续获得先补亏空。
    __table_args__ = ()

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    balance: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class PointsHistory(Base, TimestampMixin):
    __tablename__ = "points_history"
    __table_args__ = (
        CheckConstraint("amount <> 0", name="ck_points_history_amount_non_zero"),
    )

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str | None] = mapped_column(String(256))
    source_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PointsBatch(Base, TimestampMixin):
    """积分批次账本：每笔获得记一个批次，先进先出消费，到期清零。

    批次的创建只在 _grant_points_in_session 内发生，与流水共用幂等键去重，
    因此本表不设独立唯一约束。
    """

    __tablename__ = "points_batch"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_points_batch_amount_positive"),
        CheckConstraint("remaining >= 0", name="ck_points_batch_remaining_non_negative"),
        CheckConstraint("remaining <= amount", name="ck_points_batch_remaining_not_exceed_amount"),
    )

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    remaining: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(String(256))
    source_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


Index(
    "uq_points_history_user_source_action",
    PointsHistory.user_id,
    PointsHistory.source_type,
    PointsHistory.source_id,
    PointsHistory.action_type,
    unique=True,
    postgresql_where=PointsHistory.source_type.is_not(None) & PointsHistory.source_id.is_not(None),
)

Index(
    "ix_points_batch_fifo",
    PointsBatch.user_id,
    PointsBatch.expires_at,
    postgresql_where=PointsBatch.remaining > 0,
)
