"""课程章节模型。"""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
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
            "duration IS NULL OR duration >= 0", name="ck_course_chapter_duration"
        ),
        CheckConstraint(
            "video_source_type IN ('external_url', 'private_object')",
            name="ck_course_chapter_video_source",
        ),
        CheckConstraint(
            "(video_source_type = 'external_url' AND video_url IS NOT NULL) OR "
            "(video_source_type = 'private_object' AND video_storage_key IS NOT NULL)",
            name="ck_course_chapter_video_source_present",
        ),
    )

    course_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("course.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    video_url: Mapped[str | None] = mapped_column(String(512))
    video_source_type: Mapped[str] = mapped_column(
        String(24), nullable=False, default="external_url", server_default="external_url"
    )
    video_storage_key: Mapped[str | None] = mapped_column(String(512))
    duration: Mapped[int | None] = mapped_column(Integer)       # 秒，前端自动获取
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(default=True)       # 软删除开关
    is_preview: Mapped[bool] = mapped_column(
        default=False, server_default="false"
    )

    course: Mapped["Course"] = relationship(back_populates="chapters")
