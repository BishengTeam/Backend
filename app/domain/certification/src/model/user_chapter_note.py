"""用户章节学习笔记模型（2026-11-26 从视频站 vw_chapter_note 收编到主库）。"""

from sqlalchemy import ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class UserChapterNote(Base, TimestampMixin):
    __tablename__ = "user_chapter_note"
    __table_args__ = (
        UniqueConstraint("user_id", "course_id", "chapter_id", name="uq_user_chapter_note"),
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
    resource_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
