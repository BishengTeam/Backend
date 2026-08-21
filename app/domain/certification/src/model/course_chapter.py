"""课程章节模型。"""

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.adapter.database import Base, TimestampMixin


class CourseChapter(Base, TimestampMixin):
    __tablename__ = "course_chapter"
    __table_args__ = (
        UniqueConstraint("course_id", "sort_order", name="uq_course_chapter_sort"),
        CheckConstraint(
            "size_bytes > 0", name="ck_course_chapter_size_positive"
        ),
        CheckConstraint(
            "duration > 0", name="ck_course_chapter_duration_positive"
        ),
        Index("ix_course_chapter_course_sort", "course_id", "sort_order", "id"),
    )

    course_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("course.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    video_storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    original_filename: Mapped[str] = mapped_column(String(256), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    duration: Mapped[int] = mapped_column(Integer, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    course: Mapped["Course"] = relationship(back_populates="chapters")
