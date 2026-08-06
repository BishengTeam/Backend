"""Frozen user-facing request and response models for the new quiz API."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.community.src.rule.quiz import (
    QuizExamStatus,
    QuizPracticeMode,
    QuizPracticeSessionStatus,
    QuizQuestionStatus,
    QuizQuestionType,
    QuizWrongStatus,
)


QuizAnswer: TypeAlias = str | list[str]


class QuizContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
        str_strip_whitespace=True,
    )


def _canonical_answer_shape(value: object) -> QuizAnswer:
    if isinstance(value, str):
        answer = value.strip().upper()
        if answer not in {"A", "B", "C", "D"}:
            raise ValueError("answer must be one of A, B, C, D")
        return answer
    if isinstance(value, list):
        normalized: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                raise ValueError("multiple-choice answers must contain strings")
            key = item.strip().upper()
            if key not in {"A", "B", "C", "D"}:
                raise ValueError("answer items must be one of A, B, C, D")
            normalized.add(key)
        if not normalized:
            raise ValueError("answer cannot be empty")
        return sorted(normalized)
    raise ValueError("answer must be a string or a string array")


class QuizCategoryNode(QuizContractModel):
    id: int
    name: str
    parent_id: int | None = None
    depth: int = Field(ge=1, le=3)
    description: str | None = None
    sort_order: int
    question_count: int = Field(ge=1)
    children: list[QuizCategoryNode] = Field(default_factory=list)


class QuizQuestionListQuery(QuizContractModel):
    category_id: int | None = Field(default=None, ge=1)
    question_type: QuizQuestionType | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class QuizPublicQuestion(QuizContractModel):
    id: int
    category_id: int
    question_type: QuizQuestionType
    question_text: str
    options: dict[str, str]


class QuizCategoryPathItem(QuizContractModel):
    id: int
    name: str


class QuizPracticeSessionCreate(QuizContractModel):
    mode: QuizPracticeMode = QuizPracticeMode.NORMAL
    category_id: int | None = Field(default=None, ge=1)
    question_count: int | None = Field(default=None, ge=10, le=100)

    @model_validator(mode="after")
    def validate_mode_fields(self) -> "QuizPracticeSessionCreate":
        if self.mode is QuizPracticeMode.NORMAL:
            if self.category_id is None or self.question_count is None:
                raise ValueError("normal practice requires category_id and question_count")
        else:
            if self.category_id is not None:
                raise ValueError("wrong practice does not accept category_id")
            self.question_count = 20
        return self


class QuizPracticeAttemptResult(QuizContractModel):
    attempt_id: int
    attempt_no: int = Field(ge=1)
    user_answer: QuizAnswer
    is_correct: bool
    correct_answer: QuizAnswer
    explanation: str
    submitted_at: datetime


class QuizPracticeQuestionState(QuizPublicQuestion):
    session_question_id: int
    position: int = Field(ge=1)
    category_path: list[QuizCategoryPathItem]
    answered: bool
    attempt_count: int = Field(ge=0)
    latest_result: QuizPracticeAttemptResult | None = None


class QuizPracticeSessionResponse(QuizContractModel):
    id: int
    mode: QuizPracticeMode
    category_id: int | None = None
    requested_count: int
    actual_count: int = Field(ge=1, le=100)
    status: QuizPracticeSessionStatus
    started_at: datetime
    completed_at: datetime | None = None
    abandoned_at: datetime | None = None
    lock_version: int = Field(ge=1)
    questions: list[QuizPracticeQuestionState]


class QuizPracticeAttemptCreate(QuizContractModel):
    session_question_id: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=64)
    user_answer: QuizAnswer

    @field_validator("user_answer")
    @classmethod
    def normalize_answer(cls, value: object) -> QuizAnswer:
        return _canonical_answer_shape(value)


class QuizPracticeAbandonResponse(QuizContractModel):
    session_id: int
    status: Literal[QuizPracticeSessionStatus.ABANDONED]
    abandoned_at: datetime


class QuizPracticeHistoryQuery(QuizContractModel):
    category_id: int | None = Field(default=None, ge=1)
    question_type: QuizQuestionType | None = None
    is_correct: bool | None = None
    date_from: date | None = None
    date_to: date | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def validate_dates(self) -> "QuizPracticeHistoryQuery":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from cannot be after date_to")
        return self


class QuizPracticeHistoryItem(QuizContractModel):
    attempt_id: int
    session_id: int
    session_question_id: int
    question_id: int
    category_path: list[QuizCategoryPathItem]
    question_type: QuizQuestionType
    question_text: str
    options: dict[str, str]
    user_answer: QuizAnswer
    correct_answer: QuizAnswer
    explanation: str
    is_correct: bool
    attempt_no: int = Field(ge=1)
    submitted_at: datetime
    current_question_status: QuizQuestionStatus | None = None


class QuizWrongBookQuery(QuizContractModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class QuizWrongBookItem(QuizContractModel):
    id: int
    question_id: int
    status: QuizWrongStatus
    question: QuizPublicQuestion
    question_status: QuizQuestionStatus
    usable_for_practice: bool
    first_wrong_at: datetime
    latest_wrong_at: datetime


class QuizCollectionCreate(QuizContractModel):
    question_id: int = Field(ge=1)


class QuizCollectionItem(QuizContractModel):
    id: int
    question_id: int
    question: QuizPublicQuestion
    question_status: QuizQuestionStatus
    is_active: bool
    collected_at: datetime


class QuizCollectionMutationResponse(QuizContractModel):
    question_id: int
    is_active: bool
    updated_at: datetime


class QuizCheckinStatusResponse(QuizContractModel):
    checkin_date: date
    checked_in: bool
    questions_completed: int = Field(ge=0)
    consecutive_days: int = Field(ge=0)


class QuizCheckinCalendarQuery(QuizContractModel):
    date_from: date
    date_to: date

    @model_validator(mode="after")
    def validate_range(self) -> "QuizCheckinCalendarQuery":
        if self.date_from > self.date_to:
            raise ValueError("date_from cannot be after date_to")
        if (self.date_to - self.date_from).days > 366:
            raise ValueError("calendar range cannot exceed 366 days")
        return self


class QuizCheckinDay(QuizContractModel):
    checkin_date: date
    questions_completed: int = Field(ge=1)
    consecutive_days: int = Field(ge=1)


class QuizPracticeStats(QuizContractModel):
    total_attempts: int = Field(ge=0)
    first_attempts: int = Field(ge=0)
    first_correct_attempts: int = Field(ge=0)
    accuracy: Decimal = Field(ge=0, le=100)
    answered_questions: int = Field(ge=0)
    active_wrong_count: int = Field(ge=0)
    active_collection_count: int = Field(ge=0)
    checkin_days: int = Field(ge=0)
    consecutive_days: int = Field(ge=0)
    today_questions: int = Field(ge=0)


class QuizExamStats(QuizContractModel):
    completed_exam_count: int = Field(ge=0)
    timed_out_exam_count: int = Field(ge=0)
    total_questions: int = Field(ge=0)
    correct_count: int = Field(ge=0)
    wrong_count: int = Field(ge=0)
    unanswered_count: int = Field(ge=0)
    average_score: Decimal | None = Field(default=None, ge=0, le=100)
    highest_score: Decimal | None = Field(default=None, ge=0, le=100)
    latest_score: Decimal | None = Field(default=None, ge=0, le=100)


class QuizStatsResponse(QuizContractModel):
    practice: QuizPracticeStats
    exam: QuizExamStats


class QuizExamCreate(QuizContractModel):
    category_id: int = Field(ge=1)
    question_count: int = Field(ge=10, le=100)


class QuizExamListQuery(QuizContractModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class QuizExamListItem(QuizContractModel):
    id: int
    category_id: int
    question_count: int = Field(ge=10, le=100)
    duration_seconds: Literal[3600]
    status: QuizExamStatus
    started_at: datetime
    deadline_at: datetime
    finished_at: datetime | None = None
    score: Decimal | None = Field(default=None, ge=0, le=100)


class QuizExamQuestionState(QuizPublicQuestion):
    exam_question_id: int
    position: int = Field(ge=1)
    category_path: list[QuizCategoryPathItem]
    user_answer: QuizAnswer | None = None
    answer_lock_version: int | None = Field(default=None, ge=1)


class QuizExamInProgressDetail(QuizContractModel):
    id: int
    status: Literal[QuizExamStatus.IN_PROGRESS]
    category_id: int
    question_count: int = Field(ge=10, le=100)
    duration_seconds: Literal[3600]
    started_at: datetime
    deadline_at: datetime
    server_time: datetime
    questions: list[QuizExamQuestionState]


class QuizExamAbandonedQuestion(QuizPublicQuestion):
    exam_question_id: int
    position: int = Field(ge=1)
    answered: bool


class QuizExamAbandonedDetail(QuizContractModel):
    id: int
    status: Literal[QuizExamStatus.ABANDONED]
    category_id: int
    question_count: int = Field(ge=10, le=100)
    duration_seconds: Literal[3600]
    started_at: datetime
    deadline_at: datetime
    abandoned_at: datetime
    questions: list[QuizExamAbandonedQuestion]


class QuizExamQuestionResult(QuizPublicQuestion):
    exam_question_id: int
    position: int = Field(ge=1)
    user_answer: QuizAnswer | None = None
    correct_answer: QuizAnswer
    explanation: str
    is_correct: bool


class QuizExamSettledDetail(QuizContractModel):
    id: int
    status: Literal[QuizExamStatus.COMPLETED, QuizExamStatus.TIMED_OUT]
    category_id: int
    question_count: int = Field(ge=10, le=100)
    duration_seconds: Literal[3600]
    started_at: datetime
    deadline_at: datetime
    finished_at: datetime
    correct_count: int = Field(ge=0)
    wrong_count: int = Field(ge=0)
    unanswered_count: int = Field(ge=0)
    score: Decimal = Field(ge=0, le=100, decimal_places=1)
    questions: list[QuizExamQuestionResult]


QuizExamDetailResponse = Annotated[
    QuizExamInProgressDetail | QuizExamAbandonedDetail | QuizExamSettledDetail,
    Field(discriminator="status"),
]


class QuizExamAnswerSave(QuizContractModel):
    user_answer: QuizAnswer
    lock_version: int = Field(ge=0)

    @field_validator("user_answer")
    @classmethod
    def normalize_answer(cls, value: object) -> QuizAnswer:
        return _canonical_answer_shape(value)


class QuizExamAnswerSaved(QuizContractModel):
    exam_id: int
    exam_question_id: int
    user_answer: QuizAnswer
    lock_version: int = Field(ge=1)
    saved_at: datetime


class QuizExamActionResponse(QuizContractModel):
    exam_id: int
    status: QuizExamStatus
    finished_at: datetime
    score: Decimal | None = Field(default=None, ge=0, le=100)
