from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class Order(Base, TimestampMixin):
    __tablename__ = "order"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'paid', 'completed', 'refunded', 'closed')",
            name="ck_order_status",
        ),
        Index("ix_order_transaction_id_unique", "transaction_id", unique=True),
        Index(
            "uq_order_active_user_plan",
            "user_id",
            "plan_id",
            unique=True,
            postgresql_where=text(
                "plan_id IS NOT NULL AND status IN ('pending', 'paid', 'completed')"
            ),
            sqlite_where=text(
                "plan_id IS NOT NULL AND status IN ('pending', 'paid', 'completed')"
            ),
        ),
    )

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    order_kind: Mapped[str] = mapped_column(String(32), nullable=False, server_default="certification", index=True)
    product_type: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("plan.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    application_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("renshe_application.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    inventory_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("inventory.id"), nullable=True, index=True
    )
    candidate_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    candidate_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    candidate_idcard: Mapped[str | None] = mapped_column(String(20))
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending", index=True)
    out_trade_no: Mapped[str | None] = mapped_column(String(64), unique=True)
    transaction_id: Mapped[str | None] = mapped_column(String(64))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[str | None] = mapped_column(String(128))
    extra_data: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="按 product_type 存的差异化报名数据")
    attachments: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="上传材料 URL 列表")
