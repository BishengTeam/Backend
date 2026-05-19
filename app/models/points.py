from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class UserPoints(Base, TimestampMixin):
    __tablename__ = "user_points"
    __table_args__ = (
        CheckConstraint("balance >= 0", name="ck_user_points_balance_non_negative"),
    )

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), nullable=False, unique=True, index=True)
    balance: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class PointsHistory(Base, TimestampMixin):
    __tablename__ = "points_history"
    __table_args__ = (
        CheckConstraint("amount <> 0", name="ck_points_history_amount_non_zero"),
        CheckConstraint(
            "balance_after >= 0",
            name="ck_points_history_balance_after_non_negative",
        ),
    )

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
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
