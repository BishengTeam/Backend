"""课程章节模型。"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.adapter.database import Base, RecycleBinMixin, TimestampMixin


class CourseChapter(Base, RecycleBinMixin, TimestampMixin):
    __tablename__ = "course_chapter"
    __table_args__ = (
        UniqueConstraint("course_id", "sort_order", name="uq_course_chapter_sort"),
        UniqueConstraint("resource_storage_key", name="uq_course_chapter_resource_key"),
        CheckConstraint(
            "size_bytes > 0", name="ck_course_chapter_size_positive"
        ),
        CheckConstraint(
            "(resource_type = 'video' AND duration IS NOT NULL AND duration > 0 AND page_count IS NULL AND video_storage_key IS NOT NULL) OR "
            "(resource_type = 'document' AND duration IS NULL AND page_count IS NOT NULL AND page_count > 0 AND video_storage_key IS NULL)", name="ck_course_chapter_resource"
        ),
        CheckConstraint("resource_version > 0", name="ck_course_chapter_version"),
        Index("ix_course_chapter_course_sort", "course_id", "sort_order", "id"),
    )

    course_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("course.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    video_storage_key: Mapped[str | None] = mapped_column(String(512), unique=True)
    resource_storage_key: Mapped[str] = mapped_column(String(512), nullable=False,
        default=lambda context: context.get_current_parameters().get("video_storage_key"))
    resource_type: Mapped[str] = mapped_column(String(16), nullable=False, default="video", server_default="video")
    resource_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    page_count: Mapped[int | None] = mapped_column(Integer)
    original_filename: Mapped[str] = mapped_column(String(256), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    duration: Mapped[int | None] = mapped_column(Integer)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    course: Mapped["Course"] = relationship(back_populates="chapters")
