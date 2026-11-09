"""批次考试时段：一个批次可含多个同日时段（上午场/下午场等）。"""
from datetime import time

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import BigInteger as _BigInteger, Integer as _Integer

from app.adapter.database import Base, TimestampMixin

_ID = _BigInteger().with_variant(_Integer(), "sqlite")


class PlanTimeSegment(Base, TimestampMixin):
    __tablename__ = "plan_time_segment"
    __table_args__ = (
        UniqueConstraint("plan_id", "label", name="uq_plan_segment_label"),
        CheckConstraint("end_time > start_time", name="ck_segment_time"),
        CheckConstraint("sort_order >= 0", name="ck_segment_sort_nonnegative"),
        Index("ix_plan_segment_plan", "plan_id", "sort_order"),
    )

    id: Mapped[int] = mapped_column(_ID, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("plan.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(32), nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
