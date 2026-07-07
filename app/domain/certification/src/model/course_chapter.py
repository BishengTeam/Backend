"""课程章节模型。"""

from sqlalchemy import String, Integer, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.adapter.database import Base, TimestampMixin


class CourseChapter(Base, TimestampMixin):
    __tablename__ = "course_chapter"
    __table_args__ = (
        UniqueConstraint("course_id", "sort_order", name="uq_course_chapter_sort"),
    )

    course_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("course.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    video_url: Mapped[str | None] = mapped_column(String(512))
    duration: Mapped[int | None] = mapped_column(Integer)       # 秒，前端自动获取
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(default=True)       # 软删除开关

    course: Mapped["Course"] = relationship(back_populates="chapters")
