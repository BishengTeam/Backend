"""考场开放计划 + 科目 + 时段 + 机位配额。"""
from datetime import date, time

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class ExamSchedule(Base, TimestampMixin):
    """考场开放计划 — 一个计划对应一个时间段的考场安排。"""
    __tablename__ = "exam_schedule"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','published','closed')",
            name="ck_exam_schedule_status",
        ),
        CheckConstraint("open_date_start <= open_date_end", name="ck_exam_schedule_dates"),
        CheckConstraint(
            "array_length(allowed_weekdays, 1) > 0",
            name="ck_exam_schedule_weekdays_not_empty",
        ),
        Index("ix_exam_schedule_status", "status"),
        Index("ix_exam_schedule_vendor", "vendor"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    vendor: Mapped[str] = mapped_column(String(32), nullable=False, default="h3c", server_default="h3c")
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    open_date_start: Mapped[date] = mapped_column(Date, nullable=False)
    open_date_end: Mapped[date] = mapped_column(Date, nullable=False)
    allowed_weekdays: Mapped[list[int]] = mapped_column(
        ARRAY(Integer), nullable=False, default=[4], server_default="{4}"
    )  # Python weekday(): 4=周五
    pre_exam_notice: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft", server_default="draft"
    )
    published_at: Mapped[date | None] = mapped_column(Date)


class ExamScheduleSubject(Base, TimestampMixin):
    """计划包含的科目。"""
    __tablename__ = "exam_schedule_subject"
    __table_args__ = (UniqueConstraint("schedule_id", "product_id", name="uq_exam_schedule_subject"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    schedule_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("exam_schedule.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("cert_product.id", ondelete="RESTRICT"), nullable=False
    )


class ExamTimeSlot(Base, TimestampMixin):
    """可预约时段。"""
    __tablename__ = "exam_time_slot"
    __table_args__ = (
        UniqueConstraint("schedule_id", "start_time", name="uq_exam_time_slot"),
        CheckConstraint("sort_order >= 0", name="ck_exam_time_slot_sort"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    schedule_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("exam_schedule.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(32), nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class ExamSeatQuota(Base, TimestampMixin):
    """机位配额 — 科目 × 时段（不绑定具体日期）。"""
    __tablename__ = "exam_seat_quota"
    __table_args__ = (
        UniqueConstraint("schedule_id", "subject_id", "slot_id", name="uq_exam_seat_quota"),
        CheckConstraint("total_seats >= 0", name="ck_exam_seat_quota_nonneg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    schedule_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("exam_schedule.id", ondelete="CASCADE"), nullable=False
    )
    subject_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("exam_schedule_subject.id", ondelete="CASCADE"), nullable=False
    )
    slot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("exam_time_slot.id", ondelete="CASCADE"), nullable=False
    )
    total_seats: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
