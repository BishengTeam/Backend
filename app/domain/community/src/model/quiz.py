"""SQLAlchemy models for the frozen quiz-domain schema."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CHAR,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base


class _QuizTimestampMixin:
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class _QuizCreatedAtMixin:
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class QuizCategory(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_category"
    __table_args__ = (
        CheckConstraint("depth BETWEEN 1 AND 3", name="ck_quiz_category_depth"),
        CheckConstraint(
            "status IN ('active', 'disabled')", name="ck_quiz_category_status"
        ),
        CheckConstraint("lock_version >= 1", name="ck_quiz_category_lock_version"),
        Index(
            "uq_quiz_category_root_name",
            "normalized_name",
            unique=True,
            postgresql_where=text("parent_id IS NULL"),
            sqlite_where=text("parent_id IS NULL"),
        ),
        Index(
            "uq_quiz_category_sibling_name",
            "parent_id",
            "normalized_name",
            unique=True,
            postgresql_where=text("parent_id IS NOT NULL"),
            sqlite_where=text("parent_id IS NOT NULL"),
        ),
        Index("ix_quiz_category_parent_sort", "parent_id", "sort_order", "id"),
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(128), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("quiz_category.id", ondelete="RESTRICT")
    )
    depth: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    description: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    ever_had_question: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    lock_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="RESTRICT"), nullable=False
    )
    updated_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="RESTRICT"), nullable=False
    )


class QuizQuestion(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_question"
    __table_args__ = (
        CheckConstraint(
            "question_type IN ('single_choice', 'multiple_choice', 'judge')",
            name="ck_quiz_question_type",
        ),
        CheckConstraint(
            "status IN ('draft', 'published', 'disabled')",
            name="ck_quiz_question_status",
        ),
        CheckConstraint(
            "((status = 'draft' AND ever_published = false "
            "AND published_at IS NULL AND disabled_at IS NULL) OR "
            "(status = 'published' AND ever_published = true "
            "AND published_at IS NOT NULL) OR "
            "(status = 'disabled' AND ever_published = true "
            "AND published_at IS NOT NULL AND disabled_at IS NOT NULL))",
            name="ck_quiz_question_lifecycle",
        ),
        CheckConstraint("lock_version >= 1", name="ck_quiz_question_lock_version"),
        UniqueConstraint(
            "category_id",
            "question_text_hash",
            name="uq_quiz_question_category_text_hash",
        ),
        Index(
            "ix_quiz_question_pool",
            "category_id",
            "status",
            "question_type",
            "id",
        ),
        Index("ix_quiz_question_updated", "updated_at", "id"),
    )

    category_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("quiz_category.id", ondelete="RESTRICT"),
        nullable=False,
    )
    question_type: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft", server_default="draft"
    )
    question_text: Mapped[str] = mapped_column(String(1024), nullable=False)
    normalized_question_text: Mapped[str] = mapped_column(String(1024), nullable=False)
    question_text_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    options: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    correct_answer: Mapped[str | list[str] | None] = mapped_column(JSONB)
    explanation: Mapped[str | None] = mapped_column(String(1024))
    ever_published: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lock_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="RESTRICT"), nullable=False
    )
    updated_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="RESTRICT"), nullable=False
    )


class QuizPracticeSession(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_practice_session"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('normal', 'wrong')", name="ck_quiz_practice_session_mode"
        ),
        CheckConstraint(
            "status IN ('in_progress', 'completed', 'abandoned')",
            name="ck_quiz_practice_session_status",
        ),
        CheckConstraint(
            "((mode = 'normal' AND category_id IS NOT NULL "
            "AND requested_count BETWEEN 10 AND 100) OR "
            "(mode = 'wrong' AND category_id IS NULL AND requested_count = 20))",
            name="ck_quiz_practice_session_request",
        ),
        CheckConstraint(
            "actual_count BETWEEN 1 AND 100",
            name="ck_quiz_practice_session_actual_count",
        ),
        CheckConstraint(
            "((status = 'in_progress' AND completed_at IS NULL AND abandoned_at IS NULL) OR "
            "(status = 'completed' AND completed_at IS NOT NULL AND abandoned_at IS NULL) OR "
            "(status = 'abandoned' AND completed_at IS NULL AND abandoned_at IS NOT NULL))",
            name="ck_quiz_practice_session_lifecycle",
        ),
        CheckConstraint(
            "lock_version >= 1", name="ck_quiz_practice_session_lock_version"
        ),
        Index(
            "uq_quiz_practice_session_active_user",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'in_progress'"),
            sqlite_where=text("status = 'in_progress'"),
        ),
        Index("ix_quiz_practice_session_user_history", "user_id", "started_at", "id"),
    )

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    category_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("quiz_category.id", ondelete="RESTRICT")
    )
    requested_count: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="in_progress", server_default="in_progress"
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    abandoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lock_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )


class QuizPracticeSessionQuestion(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_practice_session_question"
    __table_args__ = (
        CheckConstraint("position >= 1", name="ck_quiz_practice_question_position"),
        CheckConstraint(
            "question_type IN ('single_choice', 'multiple_choice', 'judge')",
            name="ck_quiz_practice_question_type",
        ),
        UniqueConstraint(
            "session_id", "position", name="uq_quiz_practice_question_position"
        ),
        UniqueConstraint(
            "session_id", "question_id", name="uq_quiz_practice_question_question"
        ),
        UniqueConstraint(
            "session_id", "id", name="uq_quiz_practice_question_session_id"
        ),
        Index("ix_quiz_practice_question_session", "session_id", "position"),
    )

    session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("quiz_practice_session.id", ondelete="CASCADE"),
        nullable=False,
    )
    question_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quiz_question.id", ondelete="RESTRICT"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    category_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    category_path: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    question_type: Mapped[str] = mapped_column(String(24), nullable=False)
    question_text: Mapped[str] = mapped_column(String(1024), nullable=False)
    options: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    correct_answer: Mapped[str | list[str]] = mapped_column(JSONB, nullable=False)
    explanation: Mapped[str] = mapped_column(String(1024), nullable=False)
    question_lock_version: Mapped[int] = mapped_column(Integer, nullable=False)


class QuizPracticeAttempt(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_practice_attempt"
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id", "session_question_id"],
            [
                "quiz_practice_session_question.session_id",
                "quiz_practice_session_question.id",
            ],
            name="fk_quiz_practice_attempt_session_question",
            ondelete="CASCADE",
        ),
        CheckConstraint("attempt_no >= 1", name="ck_quiz_practice_attempt_no"),
        CheckConstraint(
            "is_first_attempt = (attempt_no = 1)",
            name="ck_quiz_practice_attempt_first",
        ),
        UniqueConstraint(
            "user_id",
            "session_id",
            "idempotency_key",
            name="uq_quiz_practice_attempt_idempotency",
        ),
        UniqueConstraint(
            "session_question_id",
            "attempt_no",
            name="uq_quiz_practice_attempt_number",
        ),
        Index("ix_quiz_practice_attempt_user_time", "user_id", "submitted_at", "id"),
        Index(
            "ix_quiz_practice_attempt_session_question",
            "session_id",
            "session_question_id",
        ),
    )

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    session_question_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    is_first_attempt: Mapped[bool] = mapped_column(Boolean, nullable=False)
    user_answer: Mapped[str | list[str]] = mapped_column(JSONB, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class QuizWrongItem(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_wrong_item"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'cleared')", name="ck_quiz_wrong_item_status"
        ),
        CheckConstraint(
            "(status = 'active' OR "
            "(status = 'cleared' AND cleared_at IS NOT NULL))",
            name="ck_quiz_wrong_item_lifecycle",
        ),
        UniqueConstraint("user_id", "question_id", name="uq_quiz_wrong_item_user_question"),
        Index("ix_quiz_wrong_item_recent", "user_id", "status", "latest_wrong_at", "id"),
    )

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quiz_question.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )
    first_wrong_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    latest_wrong_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latest_wrong_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)


class QuizCollection(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_collection"
    __table_args__ = (
        UniqueConstraint("user_id", "question_id", name="uq_quiz_collection_user_question"),
        CheckConstraint(
            "(is_active = true OR removed_at IS NOT NULL)",
            name="ck_quiz_collection_lifecycle",
        ),
        Index("ix_quiz_collection_active", "user_id", "is_active", "collected_at", "id"),
    )

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quiz_question.id", ondelete="RESTRICT"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QuizCheckin(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_checkin"
    __table_args__ = (
        CheckConstraint(
            "questions_completed >= 1", name="ck_quiz_checkin_questions_completed"
        ),
        CheckConstraint("consecutive_days >= 1", name="ck_quiz_checkin_consecutive_days"),
        UniqueConstraint("user_id", "checkin_date", name="uq_quiz_checkin_user_date"),
        UniqueConstraint("first_attempt_id", name="uq_quiz_checkin_first_attempt"),
    )

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    checkin_date: Mapped[date] = mapped_column(Date, nullable=False)
    questions_completed: Mapped[int] = mapped_column(Integer, nullable=False)
    consecutive_days: Mapped[int] = mapped_column(Integer, nullable=False)
    first_attempt_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("quiz_practice_attempt.id", ondelete="CASCADE"),
        nullable=False,
    )


class QuizExam(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_exam"
    __table_args__ = (
        CheckConstraint(
            "status IN ('in_progress', 'completed', 'timed_out', 'abandoned')",
            name="ck_quiz_exam_status",
        ),
        CheckConstraint(
            "question_count BETWEEN 10 AND 100", name="ck_quiz_exam_question_count"
        ),
        CheckConstraint("duration_seconds = 3600", name="ck_quiz_exam_duration"),
        CheckConstraint(
            "deadline_at = started_at + INTERVAL '3600 seconds'",
            name="ck_quiz_exam_deadline",
        ),
        CheckConstraint("lock_version >= 1", name="ck_quiz_exam_lock_version"),
        CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 100)",
            name="ck_quiz_exam_score",
        ),
        CheckConstraint(
            "((status IN ('in_progress', 'abandoned') AND correct_count IS NULL "
            "AND wrong_count IS NULL AND unanswered_count IS NULL AND score IS NULL) OR "
            "(status IN ('completed', 'timed_out') AND correct_count IS NOT NULL "
            "AND wrong_count IS NOT NULL AND unanswered_count IS NOT NULL "
            "AND score IS NOT NULL AND correct_count >= 0 AND wrong_count >= 0 "
            "AND unanswered_count >= 0 AND "
            "correct_count + wrong_count + unanswered_count = question_count))",
            name="ck_quiz_exam_result_state",
        ),
        CheckConstraint(
            "((status = 'in_progress' AND submitted_at IS NULL AND timed_out_at IS NULL "
            "AND abandoned_at IS NULL) OR "
            "(status = 'completed' AND submitted_at IS NOT NULL AND timed_out_at IS NULL "
            "AND abandoned_at IS NULL) OR "
            "(status = 'timed_out' AND submitted_at IS NULL AND timed_out_at IS NOT NULL "
            "AND abandoned_at IS NULL) OR "
            "(status = 'abandoned' AND submitted_at IS NULL AND timed_out_at IS NULL "
            "AND abandoned_at IS NOT NULL))",
            name="ck_quiz_exam_lifecycle",
        ),
        Index(
            "uq_quiz_exam_active_user",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'in_progress'"),
            sqlite_where=text("status = 'in_progress'"),
        ),
        Index("ix_quiz_exam_deadline", "status", "deadline_at", "id"),
        Index("ix_quiz_exam_user_history", "user_id", "started_at", "id"),
    )

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    category_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("quiz_category.id", ondelete="RESTRICT"),
        nullable=False,
    )
    question_count: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3600, server_default="3600"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="in_progress", server_default="in_progress"
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timed_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    abandoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    correct_count: Mapped[int | None] = mapped_column(Integer)
    wrong_count: Mapped[int | None] = mapped_column(Integer)
    unanswered_count: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    lock_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )


class QuizExamQuestion(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_exam_question"
    __table_args__ = (
        CheckConstraint("position >= 1", name="ck_quiz_exam_question_position"),
        CheckConstraint(
            "question_type IN ('single_choice', 'multiple_choice', 'judge')",
            name="ck_quiz_exam_question_type",
        ),
        UniqueConstraint("exam_id", "position", name="uq_quiz_exam_question_position"),
        UniqueConstraint("exam_id", "question_id", name="uq_quiz_exam_question_question"),
        UniqueConstraint("exam_id", "id", name="uq_quiz_exam_question_exam_id"),
        Index("ix_quiz_exam_question_exam", "exam_id", "position"),
    )

    exam_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quiz_exam.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quiz_question.id", ondelete="RESTRICT"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    category_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    category_path: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    question_type: Mapped[str] = mapped_column(String(24), nullable=False)
    question_text: Mapped[str] = mapped_column(String(1024), nullable=False)
    options: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    correct_answer: Mapped[str | list[str]] = mapped_column(JSONB, nullable=False)
    explanation: Mapped[str] = mapped_column(String(1024), nullable=False)
    question_lock_version: Mapped[int] = mapped_column(Integer, nullable=False)


class QuizExamAnswer(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_exam_answer"
    __table_args__ = (
        ForeignKeyConstraint(
            ["exam_id", "exam_question_id"],
            ["quiz_exam_question.exam_id", "quiz_exam_question.id"],
            name="fk_quiz_exam_answer_exam_question",
            ondelete="CASCADE",
        ),
        CheckConstraint("lock_version >= 1", name="ck_quiz_exam_answer_lock_version"),
        UniqueConstraint("exam_question_id", name="uq_quiz_exam_answer_question"),
        Index("ix_quiz_exam_answer_exam", "exam_id", "exam_question_id"),
    )

    exam_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    exam_question_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_answer: Mapped[str | list[str]] = mapped_column(JSONB, nullable=False)
    is_correct: Mapped[bool | None] = mapped_column(Boolean)
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lock_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )


class QuizUserStats(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_user_stats"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_quiz_user_stats_user"),
        CheckConstraint(
            "practice_total_attempts >= 0 AND practice_first_attempts >= 0 "
            "AND practice_first_correct >= 0 AND practice_answered_questions >= 0 "
            "AND active_wrong_count >= 0 AND active_collection_count >= 0 "
            "AND checkin_days >= 0 AND consecutive_days >= 0 "
            "AND today_practice_count >= 0 AND completed_exam_count >= 0 "
            "AND timed_out_exam_count >= 0 AND exam_total_questions >= 0 "
            "AND exam_correct >= 0 AND exam_wrong >= 0 AND exam_unanswered >= 0 "
            "AND exam_score_sum >= 0 AND practice_first_correct <= practice_first_attempts "
            "AND exam_correct + exam_wrong + exam_unanswered = exam_total_questions "
            "AND (exam_high_score IS NULL OR (exam_high_score >= 0 AND exam_high_score <= 100)) "
            "AND (exam_latest_score IS NULL OR (exam_latest_score >= 0 AND exam_latest_score <= 100))",
            name="ck_quiz_user_stats_nonnegative",
        ),
    )

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    practice_total_attempts: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    practice_first_attempts: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    practice_first_correct: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    practice_answered_questions: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    active_wrong_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    active_collection_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    checkin_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    consecutive_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    today_practice_date: Mapped[date | None] = mapped_column(Date)
    today_practice_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    completed_exam_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    timed_out_exam_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    exam_total_questions: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    exam_correct: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    exam_wrong: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    exam_unanswered: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    exam_score_sum: Mapped[Decimal] = mapped_column(
        Numeric(14, 1), nullable=False, default=0, server_default="0"
    )
    exam_high_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    exam_latest_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    exam_latest_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QuizQuestionStats(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_question_stats"
    __table_args__ = (
        UniqueConstraint("question_id", name="uq_quiz_question_stats_question"),
        CheckConstraint(
            "practice_first_attempts >= 0 AND practice_first_correct >= 0 "
            "AND exam_answers >= 0 AND exam_correct >= 0 "
            "AND practice_first_correct <= practice_first_attempts "
            "AND exam_correct <= exam_answers",
            name="ck_quiz_question_stats_nonnegative",
        ),
    )

    question_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quiz_question.id", ondelete="CASCADE"), nullable=False
    )
    practice_first_attempts: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    practice_first_correct: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    exam_answers: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    exam_correct: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    aggregated_through: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QuizImportJob(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_import_job"
    __table_args__ = (
        CheckConstraint("source_type IN ('csv', 'json')", name="ck_quiz_import_source_type"),
        CheckConstraint(
            "status IN ('queued', 'validating', 'validation_failed', 'importing', "
            "'succeeded', 'failed')",
            name="ck_quiz_import_status",
        ),
        CheckConstraint(
            "source_size_bytes BETWEEN 1 AND 10485760",
            name="ck_quiz_import_source_size",
        ),
        CheckConstraint(
            "total_rows BETWEEN 0 AND 5000 AND validated_rows BETWEEN 0 AND 5000 "
            "AND created_count BETWEEN 0 AND 5000 AND error_count >= 0 "
            "AND validated_rows <= total_rows AND created_count <= total_rows",
            name="ck_quiz_import_counts",
        ),
        CheckConstraint("retry_count >= 0", name="ck_quiz_import_retry_count"),
        UniqueConstraint("import_batch_key", name="uq_quiz_import_batch_key"),
        Index("ix_quiz_import_job_queue", "status", "created_at", "id"),
        Index("ix_quiz_import_job_admin", "admin_id", "created_at", "id"),
        Index("ix_quiz_import_job_expiry", "expires_at", "id"),
    )

    admin_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="SET NULL")
    )
    import_batch_key: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="queued", server_default="queued"
    )
    source_object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    source_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    report_object_key: Mapped[str | None] = mapped_column(String(512))
    total_rows: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    validated_rows: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    error_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    error_message: Mapped[str | None] = mapped_column(String(1024))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class QuizAdminAuditLog(Base, _QuizCreatedAtMixin):
    __tablename__ = "quiz_admin_audit_log"
    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('admin', 'system')", name="ck_quiz_audit_actor_type"
        ),
        CheckConstraint(
            "result IN ('succeeded', 'failed')", name="ck_quiz_audit_result"
        ),
        CheckConstraint(
            "((actor_type = 'admin' AND admin_id IS NOT NULL) OR "
            "(actor_type = 'system' AND admin_id IS NULL))",
            name="ck_quiz_audit_actor",
        ),
        Index("ix_quiz_audit_object", "object_type", "object_id", "created_at"),
        Index("ix_quiz_audit_actor", "actor_type", "admin_id", "created_at"),
        Index("ix_quiz_audit_action", "action", "created_at"),
    )

    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    admin_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="RESTRICT")
    )
    permission: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    ip_address: Mapped[str | None] = mapped_column(String(45))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[int | None] = mapped_column(BigInteger)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    changed_fields: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    target_ids: Mapped[list[int] | None] = mapped_column(JSONB)
    error_summary: Mapped[str | None] = mapped_column(Text)


# Temporary source-compatibility alias for the pre-rewrite service modules. It
# does not create or preserve the removed quiz_record table or API contract.
QuizRecord = QuizPracticeAttempt


__all__ = [
    "QuizAdminAuditLog",
    "QuizCategory",
    "QuizCheckin",
    "QuizCollection",
    "QuizExam",
    "QuizExamAnswer",
    "QuizExamQuestion",
    "QuizImportJob",
    "QuizPracticeAttempt",
    "QuizPracticeSession",
    "QuizPracticeSessionQuestion",
    "QuizQuestion",
    "QuizQuestionStats",
    "QuizRecord",
    "QuizUserStats",
    "QuizWrongItem",
]
