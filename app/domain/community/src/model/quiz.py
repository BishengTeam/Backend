from datetime import date, datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, Date, String
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


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


class QuizExam(Base, TimestampMixin):
    __tablename__ = "quiz_exam"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    question_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    answers: Mapped[dict | None] = mapped_column(JSON)
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    correct_count: Mapped[int | None] = mapped_column(Integer)
    wrong_count: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[float | None] = mapped_column(Float)
    elapsed_seconds: Mapped[int | None] = mapped_column(Integer)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="in_progress", server_default="in_progress")
