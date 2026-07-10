from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class Plan(Base, TimestampMixin):
    __tablename__ = "plan"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'published', 'archived', 'cancelled')",
            name="ck_plan_status",
        ),
        Index("uq_plan_product_name", "product_type", "name", unique=True),
        Index("ix_plan_status", "status"),
    )

    product_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    apply_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    apply_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exam_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", server_default="draft")
