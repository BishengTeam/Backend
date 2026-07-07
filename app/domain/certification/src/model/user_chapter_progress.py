"""用户章节学习进度模型。"""

from sqlalchemy import Integer, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class UserChapterProgress(Base, TimestampMixin):
    __tablename__ = "user_chapter_progress"
    __table_args__ = (
        UniqueConstraint("user_id", "course_id", "chapter_id", name="uq_user_chapter_progress"),
    )

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=False, index=True
    )
    course_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("course.id"), nullable=False, index=True
    )
    chapter_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("course_chapter.id"), nullable=False
    )
    last_position_seconds: Mapped[int] = mapped_column(Integer, default=0)
    is_completed: Mapped[bool] = mapped_column(default=False)
