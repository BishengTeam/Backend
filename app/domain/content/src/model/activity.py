from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class Activity(Base, TimestampMixin):
    __tablename__ = "activity"

    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    cover_url: Mapped[str | None] = mapped_column(String(512))
    location: Mapped[str | None] = mapped_column(String(256))
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_participants: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # ── 线上营销活动扩展 ──
    related_cert_id: Mapped[int | None] = mapped_column(
        ForeignKey("certification.id", ondelete="SET NULL")
    )
    related_course_id: Mapped[int | None] = mapped_column(
        ForeignKey("course.id", ondelete="SET NULL")
    )
    live_url: Mapped[str | None] = mapped_column(String(512))
    group_qrcode_url: Mapped[str | None] = mapped_column(String(512))
    registration_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ActivityRegistration(Base, TimestampMixin):
    __tablename__ = "activity_registration"

    activity_id: Mapped[int] = mapped_column(Integer, ForeignKey("activity.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    remark: Mapped[str | None] = mapped_column(Text)


class ActivityReminder(Base, TimestampMixin):
    __tablename__ = "activity_reminder"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    activity_id: Mapped[int] = mapped_column(Integer, ForeignKey("activity.id"), nullable=False, index=True)
    reminded: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
