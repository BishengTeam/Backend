from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class Plan(Base, TimestampMixin):
    __tablename__ = "plan"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'published', 'registration_closed', "
            "'finalized', 'archived', 'cancelled')",
            name="ck_plan_status",
        ),
        CheckConstraint("price_cents >= 0", name="ck_plan_price_nonnegative"),
        CheckConstraint("capacity >= 0", name="ck_plan_capacity_nonnegative"),
        CheckConstraint("sort_order >= 0", name="ck_plan_sort_order_nonnegative"),
        Index("uq_plan_product_name", "product_type", "name", unique=True),
        Index("ix_plan_status", "status"),
    )

    product_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    apply_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    apply_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exam_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    occupation_name: Mapped[str] = mapped_column(
        String(64), nullable=False, default="信息安全管理员", server_default="信息安全管理员"
    )
    occupation_code: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
        default="4-04-04-02-网络与信息安全管理员-信息安全管理员-中级工",
        server_default="4-04-04-02-网络与信息安全管理员-信息安全管理员-中级工",
    )
    skill_level: Mapped[str] = mapped_column(
        String(32), nullable=False, default="中级工", server_default="中级工"
    )
    exam_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="新考", server_default="新考"
    )
    application_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="学历型", server_default="学历型"
    )
    price_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, default=50000, server_default="50000"
    )
    exam_location: Mapped[str | None] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text)
    contact_name: Mapped[str | None] = mapped_column(String(64))
    contact_phone: Mapped[str | None] = mapped_column(String(20))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    registration_closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleanup_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", server_default="draft")
