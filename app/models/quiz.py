from datetime import date

from sqlalchemy import String, Integer, Boolean, JSON, Date, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class QuizCategory(Base, TimestampMixin):
    __tablename__ = "quiz_category"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("quiz_category.id"), index=True)
    description: Mapped[str | None] = mapped_column(String(256))


class QuizQuestion(Base, TimestampMixin):
    __tablename__ = "quiz_question"

    category_id: Mapped[int] = mapped_column(Integer, ForeignKey("quiz_category.id"), nullable=False, index=True)
    question_type: Mapped[str] = mapped_column(String(16), nullable=False)
    question_text: Mapped[str] = mapped_column(String(1024), nullable=False)
    options: Mapped[dict | None] = mapped_column(JSON)
    correct_answer: Mapped[str] = mapped_column(String(256), nullable=False)
    explanation: Mapped[str | None] = mapped_column(String(1024))


class QuizRecord(Base, TimestampMixin):
    __tablename__ = "quiz_record"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    question_id: Mapped[int] = mapped_column(Integer, ForeignKey("quiz_question.id"), nullable=False)
    user_answer: Mapped[str | None] = mapped_column(String(256))
    is_correct: Mapped[bool | None] = mapped_column(Boolean)
    is_collected: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_wrong: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class QuizCheckin(Base, TimestampMixin):
    __tablename__ = "quiz_checkin"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    checkin_date: Mapped[date] = mapped_column(Date, nullable=False)
    questions_completed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    consecutive_days: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
