"""Frozen user-facing request and response models for the new quiz API."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

from app.domain.community.src.rule.quiz import (
    QuizExamStatus,
    QuizPracticeMode,
    QuizPracticeScopeType,
    QuizPracticeSessionStatus,
    QuizQuestionStatus,
    QuizQuestionType,
    QuizWrongStatus,
)


QuizAnswer: TypeAlias = str | list[str]
QuizPracticeScopeType: TypeAlias = Literal["library", "module", "knowledge_point"]


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


class QuizKnowledgePointCatalogItem(QuizContractModel):
    id: int
    module_id: int
    name: str
    description: str | None = None
    sort_order: int
    question_count: int = Field(ge=1)


class QuizModuleCatalogItem(QuizContractModel):
    id: int
    library_id: int
    name: str
    description: str | None = None
    sort_order: int
    question_count: int = Field(ge=1)
    knowledge_points: list[QuizKnowledgePointCatalogItem]


class QuizLibraryCatalogItem(QuizContractModel):
    id: int
    library_code: str
    name: str
    description: str
    cover_url: str
    access_mode: Literal["free", "course_entitlement"]
    question_count: int = Field(ge=1)
    module_count: int = Field(ge=1)


class QuizLibraryCatalogDetail(QuizLibraryCatalogItem):
    details: str | None = None
    modules: list[QuizModuleCatalogItem]


class QuizScopeProgressBase(QuizContractModel):
    question_count: int = Field(ge=0)
    answered_questions: int = Field(ge=0)
    accuracy: Decimal = Field(ge=0, le=100)

    @model_validator(mode="after")
    def _validate_counts(self) -> "QuizScopeProgressBase":
        if self.answered_questions > self.question_count:
            raise ValueError("scope progress cannot exceed the question total")
        return self


class QuizKnowledgePointProgress(QuizScopeProgressBase):
    knowledge_point_id: int


class QuizModuleProgress(QuizScopeProgressBase):
    module_id: int
    knowledge_points: list[QuizKnowledgePointProgress]


class QuizLibraryProgressResponse(QuizScopeProgressBase):
    library_id: int
    modules: list[QuizModuleProgress]


class QuizQuestionListQuery(QuizContractModel):
    category_id: int | None = Field(default=None, ge=1)
    question_type: QuizQuestionType | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class QuizLibraryQuestionQuery(QuizContractModel):
    scope_type: QuizPracticeScopeType = "library"
    scope_id: int | None = Field(default=None, ge=1)
    question_type: QuizQuestionType | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def validate_scope(self) -> "QuizLibraryQuestionQuery":
        if self.scope_type == "library":
            if self.scope_id is not None:
                raise ValueError("library scope does not accept scope_id")
        elif self.scope_id is None:
            raise ValueError("module and knowledge_point scopes require scope_id")
        return self


class QuizPublicQuestion(QuizContractModel):
    id: int
    category_id: int | None = None
    library_id: int | None = None
    knowledge_point_id: int | None = None
    question_revision_id: int | None = None
    question_type: QuizQuestionType
    question_text: str
    options: dict[str, str]
    image_urls: list[str] = Field(default_factory=list)
    option_image_urls: dict[str, str] = Field(default_factory=dict)


class QuizCategoryPathItem(QuizContractModel):
    id: int
    name: str
    kind: Literal["library", "module", "knowledge_point", "category"] | None = None


class QuizPracticeSessionCreate(QuizContractModel):
    mode: QuizPracticeMode = QuizPracticeMode.NORMAL
    category_id: int | None = Field(default=None, ge=1)
    question_count: int | None = Field(default=None, ge=10, le=100)
    scope_type: QuizPracticeScopeType | None = None
    scope_id: int | None = Field(default=None, ge=1)
    restart_existing: bool = False
    confirm_large_scope: bool = False

    @model_validator(mode="after")
    def validate_mode_fields(self) -> "QuizPracticeSessionCreate":
        if self.mode is QuizPracticeMode.NORMAL:
            if self.category_id is None or self.question_count is None:
                raise ValueError("normal practice requires category_id and question_count")
            if self.scope_type is not None or self.scope_id is not None:
                raise ValueError("legacy normal practice does not accept V2 scope")
        elif self.mode is QuizPracticeMode.WRONG:
            if self.category_id is not None:
                raise ValueError("wrong practice does not accept category_id")
            if self.scope_type is not None or self.scope_id is not None:
                raise ValueError("legacy wrong practice does not accept V2 scope")
            self.question_count = 20
        elif self.mode in {QuizPracticeMode.FULL, QuizPracticeMode.WRONG_ONLY}:
            if self.scope_type is None or self.scope_id is None:
                raise ValueError("V2 full practice requires scope_type and scope_id")
            if self.category_id is not None or self.question_count is not None:
                raise ValueError("V2 full practice does not accept category_id or question_count")
        else:
            raise ValueError("legacy_limited sessions cannot be created by clients")
        return self


class QuizPracticeScopePreview(QuizContractModel):
    library_id: int
    scope_type: QuizPracticeScopeType
    scope_id: int
    mode: Literal[QuizPracticeMode.FULL, QuizPracticeMode.WRONG_ONLY]
    question_count: int = Field(ge=0)
    estimated_minutes: int = Field(ge=0)
    valid_days: Literal[7] = 7
    requires_large_scope_confirmation: bool
    unfinished_session_id: int | None = None
    unfinished_session_expires_at: datetime | None = None


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
    user_answer: QuizAnswer | None = None
    answer_lock_version: int = Field(default=0, ge=0)
    correct_answer: QuizAnswer | None = None
    explanation: str | None = None
    is_correct: bool | None = None
    attempt_count: int = Field(ge=0)
    latest_result: QuizPracticeAttemptResult | None = None

    @model_serializer(mode="wrap")
    def hide_unsettled_result_fields(self, handler):
        """Omit answer keys until final submission instead of emitting null keys."""

        payload = handler(self)
        for field_name in ("correct_answer", "explanation", "is_correct"):
            if getattr(self, field_name) is None:
                payload.pop(field_name, None)
        return payload


class QuizPracticeSessionResponse(QuizContractModel):
    id: int
    mode: QuizPracticeMode
    category_id: int | None = None
    requested_count: int
    actual_count: int = Field(ge=1)
    library_id: int | None = None
    scope_type: QuizPracticeScopeType | None = None
    scope_id: int | None = None
    status: QuizPracticeSessionStatus
    started_at: datetime
    completed_at: datetime | None = None
    abandoned_at: datetime | None = None
    expires_at: datetime | None = None
    paused_at: datetime | None = None
    pause_reason: str | None = None
    answered_count: int = Field(default=0, ge=0)
    remaining_count: int = Field(default=0, ge=0)
    current_position: int | None = Field(default=None, ge=1)
    created_new: bool = True
    resume_available: bool = False
    lock_version: int = Field(ge=1)
    questions: list[QuizPracticeQuestionState]

    @model_validator(mode="after")
    def validate_result_visibility(self) -> "QuizPracticeSessionResponse":
        settled = self.status == QuizPracticeSessionStatus.COMPLETED
        for question in self.questions:
            if not settled and any(
                value is not None
                for value in (
                    question.correct_answer,
                    question.explanation,
                    question.is_correct,
                )
            ):
                raise ValueError(
                    "practice results are visible only after final submission"
                )
            if settled:
                if question.correct_answer is None or question.explanation is None:
                    raise ValueError(
                        "completed practice questions require answer and explanation"
                    )
                if (question.user_answer is None) != (question.is_correct is None):
                    raise ValueError(
                        "practice correctness must match whether the question was answered"
                    )
        return self


class QuizPracticeSkipResponse(QuizContractModel):
    session_id: int
    session_question_id: int
    skip_count: Literal[1]
    next_question: QuizPracticeQuestionState | None = None


class QuizPracticeAttemptCreate(QuizContractModel):
    session_question_id: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=64)
    user_answer: QuizAnswer

    @field_validator("user_answer")
    @classmethod
    def normalize_answer(cls, value: object) -> QuizAnswer:
        return _canonical_answer_shape(value)


class QuizPracticeAnswerSave(QuizContractModel):
    user_answer: QuizAnswer
    lock_version: int = Field(ge=0)

    @field_validator("user_answer")
    @classmethod
    def normalize_answer(cls, value: object) -> QuizAnswer:
        return _canonical_answer_shape(value)


class QuizPracticeAnswerSaved(QuizContractModel):
    session_id: int
    session_question_id: int
    user_answer: QuizAnswer
    lock_version: int = Field(ge=1)
    saved_at: datetime


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


class QuizStatsQuery(QuizContractModel):
    scope_type: QuizPracticeScopeType | None = None
    scope_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_scope(self) -> "QuizStatsQuery":
        if (self.scope_type is None) != (self.scope_id is None):
            raise ValueError("scope_type and scope_id must be provided together")
        return self


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


class QuizExamScopeSelection(QuizContractModel):
    scope_type: QuizPracticeScopeType
    scope_id: int = Field(ge=1)


class QuizManualExamCreate(QuizContractModel):
    question_ids: list[Annotated[int, Field(ge=1)]] = Field(
        min_length=10, max_length=100
    )

    @model_validator(mode="after")
    def validate_question_ids(self) -> "QuizManualExamCreate":
        if len(set(self.question_ids)) != len(self.question_ids):
            raise ValueError("question_ids must not contain duplicates")
        return self


class QuizExamCreate(QuizContractModel):
    category_id: int | None = Field(default=None, ge=1)
    scope_type: QuizPracticeScopeType | None = None
    scope_id: int | None = Field(default=None, ge=1)
    scopes: list[QuizExamScopeSelection] | None = Field(
        default=None, min_length=1, max_length=20
    )
    question_count: int = Field(ge=10, le=100)

    @model_validator(mode="after")
    def validate_scope(self) -> "QuizExamCreate":
        legacy = self.category_id is not None
        v2 = self.scope_type is not None or self.scope_id is not None
        multi = self.scopes is not None
        if sum(bool(item) for item in (legacy, v2, multi)) != 1:
            raise ValueError(
                "exactly one of category_id, scope_type + scope_id, or scopes is required"
            )
        if v2 and (self.scope_type is None or self.scope_id is None):
            raise ValueError("V2 exam requires scope_type and scope_id")
        if multi and len({(item.scope_type, item.scope_id) for item in self.scopes}) != len(self.scopes):
            raise ValueError("scopes must not contain duplicates")
        return self


class QuizExamListQuery(QuizContractModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class QuizExamListItem(QuizContractModel):
    id: int
    category_id: int | None = None
    library_id: int | None = None
    scope_type: QuizPracticeScopeType | None = None
    scope_id: int | None = None
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
    category_id: int | None = None
    library_id: int | None = None
    scope_type: QuizPracticeScopeType | None = None
    scope_id: int | None = None
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
    category_id: int | None = None
    library_id: int | None = None
    scope_type: QuizPracticeScopeType | None = None
    scope_id: int | None = None
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
    category_id: int | None = None
    library_id: int | None = None
    scope_type: QuizPracticeScopeType | None = None
    scope_id: int | None = None
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
