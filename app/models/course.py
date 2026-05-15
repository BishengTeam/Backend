from sqlalchemy import String, Integer, JSON, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Course(Base, TimestampMixin):
    __tablename__ = "course"

    title: Mapped[str] = mapped_column(String(256), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    cover_url: Mapped[str | None] = mapped_column(String(512))
    video_url: Mapped[str | None] = mapped_column(String(512))
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    batches: Mapped[dict | None] = mapped_column(JSON)
    teacher_name: Mapped[str | None] = mapped_column(String(64))
    teacher_contact: Mapped[str | None] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")


class CourseEnrollment(Base, TimestampMixin):
    __tablename__ = "course_enrollment"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    course_id: Mapped[int] = mapped_column(Integer, ForeignKey("course.id"), nullable=False)
    batch_selected: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="enrolled", server_default="enrolled")
    learning_access: Mapped[bool] = mapped_column(default=True, server_default="true")
