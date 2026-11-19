"""考生预约记录。"""
from datetime import date

from sqlalchemy import String
from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class ExamBooking(Base, TimestampMixin):
    __tablename__ = "exam_booking"
    __table_args__ = (
        CheckConstraint(
            "status IN ('booked','cancelled','completed','no_show')",
            name="ck_exam_booking_status",
        ),
        # One active reservation per vendor registration and schedule. H3C uses
        # H3cRegistration.id and Sangfor uses Order.id, so the vendor column is
        # part of the key and registration_id intentionally has no foreign key.
        UniqueConstraint(
            "vendor", "registration_id", "schedule_id", name="uq_exam_booking_once"
        ),
        Index(
            "ix_exam_booking_vendor_registration", "vendor", "registration_id"
        ),
        Index(
            "ix_exam_booking_lookup",
            "schedule_id", "exam_date", "slot_id", "subject_id", "status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    vendor: Mapped[str] = mapped_column(
        String(32), nullable=False, default="h3c", server_default="h3c"
    )
    registration_id: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    schedule_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("exam_schedule.id", ondelete="RESTRICT"), nullable=False
    )
    subject_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("exam_schedule_subject.id", ondelete="RESTRICT"), nullable=False
    )
    slot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("exam_time_slot.id", ondelete="RESTRICT"), nullable=False
    )
    exam_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="booked", server_default="booked"
    )
