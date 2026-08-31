"""SQLAlchemy models for course-bound quiz assignments and manual review."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base


class _AssignmentTimestampMixin:
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class QuizCourseAssignment(Base, _AssignmentTimestampMixin):
    """The current, library-scoped course assignment configuration."""

    __tablename__ = "quiz_course_assignment"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'published', 'disabled')",
            name="ck_quiz_course_assignment_status",
        ),
        CheckConstraint("version_no >= 1", name="ck_quiz_course_assignment_version"),
        CheckConstraint("lock_version >= 1", name="ck_quiz_course_assignment_lock"),
        CheckConstraint(
            "total_score = 100 AND essay_total_score >= 0 "
            "AND essay_total_score <= 100 "
            "AND objective_total_score >= 0 AND objective_total_score <= 100 "
            "AND essay_total_score + objective_total_score = total_score",
            name="ck_quiz_course_assignment_score_total",
        ),
        UniqueConstraint("library_id", name="uq_quiz_course_assignment_library"),
        Index("ix_quiz_course_assignment_status", "status", "id"),
    )

    library_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("quiz_library.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft", server_default="draft"
    )
    version_no: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    question_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    essay_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    objective_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    essay_total_score: Mapped[Decimal] = mapped_column(
        Numeric(7, 2), nullable=False, default=0, server_default="0"
    )
    objective_total_score: Mapped[Decimal] = mapped_column(
        Numeric(7, 2), nullable=False, default=0, server_default="0"
    )
    total_score: Mapped[Decimal] = mapped_column(
        Numeric(7, 2), nullable=False, default=100, server_default="100"
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lock_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="SET NULL")
    )
    updated_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="SET NULL")
    )


class QuizCourseAssignmentQuestion(Base, _AssignmentTimestampMixin):
    """Current question and score projection used by unsubmitted students."""

    __tablename__ = "quiz_course_assignment_question"
    __table_args__ = (
        CheckConstraint("position >= 1", name="ck_quiz_course_assignment_position"),
        CheckConstraint(
            "score >= 0 AND score <= 100",
            name="ck_quiz_course_assignment_question_score",
        ),
        UniqueConstraint("assignment_id", "question_id"),
        UniqueConstraint("assignment_id", "position"),
        UniqueConstraint("assignment_id", "id"),
        Index(
            "ix_quiz_course_assignment_question_pool",
            "assignment_id",
            "position",
            "id",
        ),
    )

    assignment_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("quiz_course_assignment.id", ondelete="CASCADE"),
        nullable=False,
    )
    question_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("quiz_question.id", ondelete="RESTRICT"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    is_essay: Mapped[bool] = mapped_column(Boolean, nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)


class QuizCourseAssignmentVersion(Base):
    __tablename__ = "quiz_course_assignment_version"
    __table_args__ = (
        CheckConstraint("version_no >= 1", name="ck_quiz_assignment_version_no"),
        CheckConstraint("question_count >= 1", name="ck_quiz_assignment_version_count"),
        CheckConstraint(
            "total_score = 100",
            name="ck_quiz_assignment_version_total",
        ),
        UniqueConstraint("assignment_id", "version_no"),
        UniqueConstraint("assignment_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    assignment_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("quiz_course_assignment.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    question_count: Mapped[int] = mapped_column(Integer, nullable=False)
    essay_count: Mapped[int] = mapped_column(Integer, nullable=False)
    objective_count: Mapped[int] = mapped_column(Integer, nullable=False)
    essay_total_score: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    objective_total_score: Mapped[Decimal] = mapped_column(
        Numeric(7, 2), nullable=False
    )
    total_score: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    config_snapshot: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="SET NULL")
    )


class QuizCourseAssignmentSubmission(Base, _AssignmentTimestampMixin):
    """One student's library-scoped assignment attempt."""

    __tablename__ = "quiz_course_assignment_submission"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'submitted', 'claimed', 'graded')",
            name="ck_quiz_assignment_submission_status",
        ),
        CheckConstraint(
            "total_score IS NULL OR (total_score >= 0 AND total_score <= 100)",
            name="ck_quiz_assignment_submission_score",
        ),
        CheckConstraint("lock_version >= 1", name="ck_quiz_assignment_submission_lock"),
        CheckConstraint(
            "config_version_no >= 1",
            name="ck_quiz_assignment_submission_version",
        ),
        UniqueConstraint("assignment_id", "user_id"),
        Index(
            "ix_quiz_assignment_submission_queue",
            "assignment_id",
            "status",
            "submitted_at",
            "id",
        ),
        Index(
            "ix_quiz_assignment_submission_user",
            "user_id",
            "status",
            "updated_at",
        ),
    )

    assignment_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("quiz_course_assignment.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    config_version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="SET NULL")
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    graded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    graded_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="SET NULL")
    )
    total_score: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    lock_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )


class QuizCourseAssignmentAnswer(Base, _AssignmentTimestampMixin):
    """A server-side draft answer for an unsubmitted assignment."""

    __tablename__ = "quiz_course_assignment_answer"
    __table_args__ = (
        UniqueConstraint("submission_id", "question_id"),
        Index("ix_quiz_assignment_answer_question", "question_id", "id"),
    )

    submission_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("quiz_course_assignment_submission.id", ondelete="CASCADE"),
        nullable=False,
    )
    question_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("quiz_question.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_answer: Mapped[str | list[str] | None] = mapped_column(JSONB)
    is_answered: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class QuizCourseAssignmentSubmissionQuestion(Base, _AssignmentTimestampMixin):
    """Immutable per-question snapshot created when a student submits."""

    __tablename__ = "quiz_course_assignment_submission_question"
    __table_args__ = (
        CheckConstraint("position >= 1", name="ck_quiz_assignment_result_position"),
        CheckConstraint(
            "score >= 0 AND score <= 100",
            name="ck_quiz_assignment_result_score",
        ),
        CheckConstraint(
            "manual_score IS NULL OR (manual_score >= 0 AND manual_score <= score)",
            name="ck_quiz_assignment_manual_score",
        ),
        CheckConstraint(
            "earned_score IS NULL OR (earned_score >= 0 AND earned_score <= score)",
            name="ck_quiz_assignment_earned_score",
        ),
        UniqueConstraint("submission_id", "question_id"),
        UniqueConstraint("submission_id", "position"),
        UniqueConstraint("submission_id", "id"),
    )

    submission_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("quiz_course_assignment_submission.id", ondelete="CASCADE"),
        nullable=False,
    )
    question_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    is_essay: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_answered: Mapped[bool] = mapped_column(Boolean, nullable=False)
    requires_review: Mapped[bool] = mapped_column(Boolean, nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    user_answer: Mapped[str | list[str] | None] = mapped_column(JSONB)
    is_objective_correct: Mapped[bool | None] = mapped_column(Boolean)
    manual_score: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    review_comment: Mapped[str | None] = mapped_column(Text)
    earned_score: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))


class QuizCourseAssignmentReviewLog(Base):
    """Append-only manual review and configuration audit trail."""

    __tablename__ = "quiz_course_assignment_review_log"
    __table_args__ = (
        CheckConstraint(
            "action IN ('save', 'claim', 'complete', 'reopen')",
            name="ck_quiz_assignment_review_action",
        ),
        CheckConstraint(
            "actor_type IN ('admin', 'system')",
            name="ck_quiz_assignment_review_actor",
        ),
        CheckConstraint(
            "((actor_type = 'admin' AND admin_id IS NOT NULL) OR "
            "(actor_type = 'system' AND admin_id IS NULL))",
            name="ck_quiz_assignment_review_actor_ref",
        ),
        Index(
            "ix_quiz_assignment_review_log_submission",
            "submission_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("quiz_course_assignment_submission.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    admin_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    before: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = [
    "QuizCourseAssignment",
    "QuizCourseAssignmentAnswer",
    "QuizCourseAssignmentQuestion",
    "QuizCourseAssignmentReviewLog",
    "QuizCourseAssignmentSubmission",
    "QuizCourseAssignmentSubmissionQuestion",
    "QuizCourseAssignmentVersion",
]
