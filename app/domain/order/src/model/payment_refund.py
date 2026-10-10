from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class PaymentRefundTask(Base, TimestampMixin):
    """Asynchronous full refund for a payment received after order closure."""

    __tablename__ = "payment_refund_task"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'processing', 'succeeded', 'failed', 'manual_review')",
            name="ck_payment_refund_task_status",
        ),
        CheckConstraint("amount_cents > 0", name="ck_payment_refund_task_amount"),
        CheckConstraint("retry_count >= 0", name="ck_payment_refund_task_retry"),
        CheckConstraint(
            "length(out_refund_no) > 0 AND length(out_refund_no) <= 32",
            name="ck_payment_refund_task_refund_no",
        ),
        Index(
            "ix_payment_refund_task_due",
            "status",
            "next_attempt_at",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("order.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    out_trade_no: Mapped[str] = mapped_column(String(64), nullable=False)
    transaction_id: Mapped[str] = mapped_column(String(64), nullable=False)
    out_refund_no: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    reason: Mapped[str] = mapped_column(String(128), default="late_payment_refund")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    submit_attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    processing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    manual_takeover_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    wechat_refund_id: Mapped[str | None] = mapped_column(String(64))
    last_provider_status: Mapped[str | None] = mapped_column(String(32))
    last_error: Mapped[str | None] = mapped_column(Text)
