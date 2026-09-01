"""Frozen admin request and response models for the new quiz API."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import Field, JsonValue, field_validator, model_validator

from app.domain.community.src.rule.quiz import (
    QuizCategoryStatus,
    QuizImportSourceType,
    QuizImportStatus,
    QuizQuestionStatus,
    QuizQuestionType,
    QuizContentStatus,
    QuizLibraryAccessMode,
    QuizLibraryStatus,
    normalize_category_name,
    normalize_question_payload,
)
from app.schemas.quiz_contract import QuizAnswer, QuizContractModel


class AdminQuizCategoryQuery(QuizContractModel):
    status: QuizCategoryStatus | None = None
    parent_id: int | None = Field(default=None, ge=1)


class AdminQuizCategoryCreate(QuizContractModel):
    name: str = Field(min_length=1, max_length=128)
    parent_id: int | None = Field(default=None, ge=1)
    description: str | None = Field(default=None, max_length=256)
    sort_order: int = 0

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return normalize_category_name(value)


class AdminQuizCategoryUpdate(QuizContractModel):
    lock_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    parent_id: int | None = Field(default=None, ge=1)
    description: str | None = Field(default=None, max_length=256)
    sort_order: int | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        return normalize_category_name(value) if value is not None else None

    @model_validator(mode="after")
    def require_change(self) -> "AdminQuizCategoryUpdate":
        if not (self.model_fields_set - {"lock_version"}):
            raise ValueError("at least one category field must be supplied")
        return self


class AdminQuizCategoryStatusUpdate(QuizContractModel):
    status: QuizCategoryStatus
    lock_version: int = Field(ge=1)


class AdminQuizCategoryResponse(QuizContractModel):
    id: int
    name: str
    normalized_name: str
    parent_id: int | None = None
    depth: int = Field(ge=1, le=3)
    description: str | None = None
    status: QuizCategoryStatus
    sort_order: int
    ever_had_question: bool
    lock_version: int = Field(ge=1)
    created_by: int
    updated_by: int
    created_at: datetime
    updated_at: datetime


class AdminQuizCategoryImpactQuery(QuizContractModel):
    action: Literal["disable", "move", "delete"]
    target_parent_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_action_parameters(self) -> "AdminQuizCategoryImpactQuery":
        if self.action != "move" and "target_parent_id" in self.model_fields_set:
            raise ValueError("target_parent_id is only valid for move")
        return self


class AdminQuizCategoryImpactResponse(QuizContractModel):
    category_id: int
    action: Literal["disable", "move", "delete"]
    target_parent_id: int | None = None
    descendant_category_count: int = Field(ge=0)
    draft_question_count: int = Field(ge=0)
    published_question_count: int = Field(ge=0)
    disabled_question_count: int = Field(ge=0)
    affected_new_pool_question_count: int = Field(ge=0)
    history_snapshot_affected: Literal[False] = False
    can_execute: bool
    blocking_reasons: list[str] = Field(default_factory=list)
    calculated_at: datetime


class AdminQuizQuestionQuery(QuizContractModel):
    category_id: int | None = Field(default=None, ge=1)
    include_descendants: bool = False
    library_id: int | None = Field(default=None, ge=1)
    module_id: int | None = Field(default=None, ge=1)
    knowledge_point_id: int | None = Field(default=None, ge=1)
    question_id: int | None = Field(default=None, ge=1)
    question_type: QuizQuestionType | None = None
    status: QuizQuestionStatus | None = None
    keyword: str | None = Field(default=None, min_length=1, max_length=128)
    include_deleted: bool = False
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def validate_hierarchy_filters(self) -> "AdminQuizQuestionQuery":
        v2_filters = [
            self.library_id,
            self.module_id,
            self.knowledge_point_id,
            self.question_id,
        ]
        if self.category_id is not None and any(value is not None for value in v2_filters):
            raise ValueError("legacy category_id cannot be combined with V2 hierarchy filters")
        return self


class AdminQuizQuestionCreate(QuizContractModel):
    category_id: int | None = Field(default=None, ge=1)
    knowledge_point_id: int | None = Field(default=None, ge=1)
    question_type: QuizQuestionType
    question_text: str = Field(min_length=1, max_length=1024)
    options: dict[str, str] | None = None
    correct_answer: QuizAnswer | None = None
    reference_answer: str | None = Field(default=None, max_length=5000)
    explanation: str | None = Field(default=None, max_length=1024)
    image_urls: list[str] = Field(default_factory=list, max_length=9)
    option_image_urls: dict[str, str] | None = None

    @model_validator(mode="after")
    def normalize_draft(self) -> "AdminQuizQuestionCreate":
        if (self.category_id is None) == (self.knowledge_point_id is None):
            raise ValueError("exactly one of category_id or knowledge_point_id is required")
        normalized = normalize_question_payload(
            question_type=self.question_type,
            question_text=self.question_text,
            options=self.options,
            correct_answer=self.correct_answer,
            reference_answer=self.reference_answer,
            explanation=self.explanation,
            image_urls=self.image_urls,
            option_image_urls=self.option_image_urls,
            require_publishable=False,
        )
        self.question_type = normalized.question_type
        self.question_text = normalized.question_text
        self.options = normalized.options
        self.correct_answer = normalized.correct_answer
        self.reference_answer = normalized.reference_answer
        self.explanation = normalized.explanation
        self.image_urls = normalized.image_urls
        self.option_image_urls = normalized.option_image_urls
        return self


class AdminQuizQuestionUpdate(QuizContractModel):
    lock_version: int = Field(ge=1)
    category_id: int | None = Field(default=None, ge=1)
    knowledge_point_id: int | None = Field(default=None, ge=1)
    question_type: QuizQuestionType | None = None
    question_text: str | None = Field(default=None, min_length=1, max_length=1024)
    options: dict[str, str] | None = None
    correct_answer: QuizAnswer | None = None
    reference_answer: str | None = Field(default=None, max_length=5000)
    explanation: str | None = Field(default=None, max_length=1024)
    image_urls: list[str] | None = Field(default=None, max_length=9)
    option_image_urls: dict[str, str] | None = None

    @model_validator(mode="after")
    def require_change(self) -> "AdminQuizQuestionUpdate":
        if not (self.model_fields_set - {"lock_version"}):
            raise ValueError("at least one question field must be supplied")
        if self.category_id is not None and self.knowledge_point_id is not None:
            raise ValueError("category_id and knowledge_point_id cannot be supplied together")
        return self


class AdminQuizQuestionResponse(QuizContractModel):
    id: int
    category_id: int | None = None
    library_id: int | None = None
    knowledge_point_id: int | None = None
    question_type: QuizQuestionType
    status: QuizQuestionStatus
    question_text: str
    normalized_question_text: str
    options: dict[str, str] | None = None
    correct_answer: QuizAnswer | None = None
    reference_answer: str | None = None
    explanation: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    option_image_urls: dict[str, str] = Field(default_factory=dict)
    ever_published: bool
    published_at: datetime | None = None
    disabled_at: datetime | None = None
    deleted_at: datetime | None = None
    restore_until: datetime | None = None
    current_revision_id: int | None = None
    current_revision_no: int | None = None
    pending_revision_id: int | None = None
    pending_revision_no: int | None = None
    has_pending_revision: bool = False
    lock_version: int = Field(ge=1)
    created_by: int
    updated_by: int
    created_at: datetime
    updated_at: datetime


class AdminQuizQuestionRevisionResponse(QuizContractModel):
    id: int
    question_id: int
    revision_no: int = Field(ge=1)
    status: Literal["draft", "published", "superseded", "discarded"]
    question_type: QuizQuestionType
    question_text: str
    normalized_question_text: str
    options: dict[str, str] | None = None
    correct_answer: QuizAnswer | None = None
    reference_answer: str | None = None
    explanation: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    option_image_urls: dict[str, str] = Field(default_factory=dict)
    published_at: datetime | None = None
    created_by: int | None = None
    created_at: datetime


class AdminQuizVersionRequest(QuizContractModel):
    lock_version: int = Field(ge=1)


class AdminQuizBatchTarget(QuizContractModel):
    question_id: int = Field(ge=1)
    lock_version: int = Field(ge=1)


class AdminQuizBatchRequest(QuizContractModel):
    items: list[AdminQuizBatchTarget] = Field(min_length=1, max_length=100)

    @field_validator("items")
    @classmethod
    def reject_duplicates(
        cls, value: list[AdminQuizBatchTarget]
    ) -> list[AdminQuizBatchTarget]:
        ids = [item.question_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("question_id cannot be repeated")
        return value


class AdminQuizBatchItemError(QuizContractModel):
    question_id: int
    code: int
    field: str | None = None
    message: str


class AdminQuizBatchResponse(QuizContractModel):
    succeeded: bool
    updated_count: int = Field(ge=0)
    errors: list[AdminQuizBatchItemError] = Field(default_factory=list)


class AdminQuizQuestionStatsResponse(QuizContractModel):
    question_id: int
    practice_first_attempts: int = Field(ge=0)
    practice_first_correct: int = Field(ge=0)
    practice_first_accuracy: Decimal = Field(ge=0, le=100)
    exam_answers: int = Field(ge=0)
    exam_correct: int = Field(ge=0)
    exam_accuracy: Decimal = Field(ge=0, le=100)
    aggregated_through: datetime | None = None


class AdminQuizStatsOverviewResponse(QuizContractModel):
    calculated_at: datetime
    aggregated_through: datetime | None = None
    library_count: int = Field(ge=0)
    draft_library_count: int = Field(ge=0)
    published_library_count: int = Field(ge=0)
    suspended_library_count: int = Field(ge=0)
    archived_library_count: int = Field(ge=0)
    module_count: int = Field(ge=0)
    active_module_count: int = Field(ge=0)
    disabled_module_count: int = Field(ge=0)
    knowledge_point_count: int = Field(ge=0)
    active_knowledge_point_count: int = Field(ge=0)
    disabled_knowledge_point_count: int = Field(ge=0)
    question_count: int = Field(ge=0)
    draft_question_count: int = Field(ge=0)
    published_question_count: int = Field(ge=0)
    disabled_question_count: int = Field(ge=0)
    practice_session_count: int = Field(ge=0)
    practice_first_attempts: int = Field(ge=0)
    practice_first_correct: int = Field(ge=0)
    practice_first_accuracy: Decimal = Field(ge=0, le=100)
    completed_exam_count: int = Field(ge=0)
    timed_out_exam_count: int = Field(ge=0)
    exam_answers: int = Field(ge=0)
    exam_correct: int = Field(ge=0)
    exam_accuracy: Decimal = Field(ge=0, le=100)


class AdminQuizStatsQuestionQuery(QuizContractModel):
    library_id: int | None = Field(default=None, ge=1)
    module_id: int | None = Field(default=None, ge=1)
    knowledge_point_id: int | None = Field(default=None, ge=1)
    question_type: QuizQuestionType | None = None
    status: QuizQuestionStatus | None = None
    include_deleted: bool = False
    keyword: str | None = Field(default=None, min_length=1, max_length=128)
    sort: Literal["updated_at", "practice_first_attempts", "practice_wrong_count"] = (
        "updated_at"
    )
    order: Literal["asc", "desc"] = "desc"
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class AdminQuizQuestionStatsListItem(QuizContractModel):
    question_id: int
    question_text: str
    library_id: int
    library_name: str
    module_id: int
    module_name: str
    knowledge_point_id: int
    knowledge_point_name: str
    question_type: QuizQuestionType
    status: QuizQuestionStatus
    practice_first_attempts: int = Field(ge=0)
    practice_first_correct: int = Field(ge=0)
    practice_first_accuracy: Decimal = Field(ge=0, le=100)
    exam_answers: int = Field(ge=0)
    exam_correct: int = Field(ge=0)
    exam_accuracy: Decimal = Field(ge=0, le=100)
    aggregated_through: datetime | None = None


class AdminQuizDailyStatsQuery(QuizContractModel):
    days: Literal[7, 30, 90] = 30


class AdminQuizDailyStatsItem(QuizContractModel):
    date: date
    practice_attempts: int = Field(ge=0)
    active_users: int = Field(ge=0)


class AdminQuizUserStatsQuery(QuizContractModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class AdminQuizUserStatsListItem(QuizContractModel):
    user_id: int
    nickname: str | None = None
    phone_masked: str | None = None
    practice_total_attempts: int = Field(ge=0)
    practice_first_attempts: int = Field(ge=0)
    practice_first_correct: int = Field(ge=0)
    practice_answered_questions: int = Field(ge=0)
    checkin_days: int = Field(ge=0)
    consecutive_days: int = Field(ge=0)


class AdminQuizUserPracticeQuery(QuizContractModel):
    user_id: int = Field(ge=1)
    library_id: int = Field(ge=1)
    date_from: date
    date_to: date

    @model_validator(mode="after")
    def validate_range(self) -> "AdminQuizUserPracticeQuery":
        if self.date_from > self.date_to:
            raise ValueError("date_from cannot be after date_to")
        if (self.date_to - self.date_from).days > 366:
            raise ValueError("practice query range cannot exceed 366 days")
        return self


class AdminQuizUserPracticeDay(QuizContractModel):
    date: date
    attempts: int = Field(ge=0)
    correct: int = Field(ge=0)
    accuracy: Decimal = Field(ge=0, le=100)


class AdminQuizUserExamRound(QuizContractModel):
    exam_id: int
    status: Literal["in_progress", "completed", "timed_out", "abandoned"]
    started_at: datetime
    settled_at: datetime | None = None
    question_count: int = Field(ge=10, le=100)
    correct_count: int | None = Field(default=None, ge=0)
    wrong_count: int | None = Field(default=None, ge=0)
    unanswered_count: int | None = Field(default=None, ge=0)
    score: Decimal | None = Field(default=None, ge=0, le=100)


class AdminQuizUserPracticeStats(QuizContractModel):
    user_id: int
    library_id: int
    date_from: date
    date_to: date
    total_attempts: int = Field(ge=0)
    answered_questions: int = Field(ge=0)
    first_attempts: int = Field(ge=0)
    first_correct: int = Field(ge=0)
    first_accuracy: Decimal = Field(ge=0, le=100)
    active_days: int = Field(ge=0)
    daily: list[AdminQuizUserPracticeDay]
    exam_rounds: list[AdminQuizUserExamRound]
    exam_settled_count: int = Field(ge=0)
    exam_average_score: Decimal | None = Field(default=None, ge=0, le=100)
    exam_highest_score: Decimal | None = Field(default=None, ge=0, le=100)
    exam_latest_score: Decimal | None = Field(default=None, ge=0, le=100)


class AdminQuizImageUploadCreate(QuizContractModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=128)
    size_bytes: int = Field(gt=0, le=10 * 1024 * 1024)


class AdminQuizImageUploadResponse(QuizContractModel):
    object_key: str
    upload_url: str
    public_url: str
    expires_at: datetime


class AdminQuizCsvImportMetadata(QuizContractModel):
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=1, le=10 * 1024 * 1024)


class AdminQuizImportQuestion(QuizContractModel):
    category_path: list[str] = Field(min_length=1, max_length=3)
    question_type: QuizQuestionType
    question_text: str = Field(min_length=1, max_length=1024)
    options: dict[str, str] | None = None
    correct_answer: QuizAnswer | None = None
    explanation: str | None = Field(default=None, max_length=1024)
    image_urls: object = Field(default_factory=list)
    option_image_urls: object = None

    @field_validator("category_path")
    @classmethod
    def normalize_path(cls, value: list[str]) -> list[str]:
        return [normalize_category_name(part) for part in value]

    @model_validator(mode="after")
    def normalize_question(self) -> "AdminQuizImportQuestion":
        normalized = normalize_question_payload(
            question_type=self.question_type,
            question_text=self.question_text,
            options=self.options,
            correct_answer=self.correct_answer,
            explanation=self.explanation,
            image_urls=self.image_urls,
            option_image_urls=self.option_image_urls,
            require_publishable=False,
        )
        self.question_type = normalized.question_type
        self.question_text = normalized.question_text
        self.options = normalized.options
        self.correct_answer = normalized.correct_answer
        self.explanation = normalized.explanation
        self.image_urls = normalized.image_urls
        self.option_image_urls = normalized.option_image_urls
        return self


class AdminQuizJsonImportRequest(QuizContractModel):
    library_id: int | None = Field(default=None, ge=1)
    questions: list[AdminQuizImportQuestion] = Field(min_length=1, max_length=5000)


class AdminQuizImportJobQuery(QuizContractModel):
    status: QuizImportStatus | None = None
    source_type: QuizImportSourceType | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class AdminQuizImportJobResponse(QuizContractModel):
    id: int
    admin_id: int | None = None
    library_id: int | None = None
    source_type: QuizImportSourceType
    status: QuizImportStatus
    source_size_bytes: int = Field(ge=1, le=10 * 1024 * 1024)
    total_rows: int = Field(ge=0, le=5000)
    validated_rows: int = Field(ge=0, le=5000)
    created_count: int = Field(ge=0, le=5000)
    error_count: int = Field(ge=0)
    heartbeat_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    retry_count: int = Field(ge=0)
    error_message: str | None = None
    report_available: bool
    lock_version: int = Field(ge=1)
    validation_version: int = Field(ge=0)
    impact_version: str | None = Field(default=None, min_length=64, max_length=64)
    missing_category_count: int = Field(ge=0, le=500)
    affected_question_count: int = Field(ge=0, le=5000)
    confirmed_by: int | None = None
    confirmed_at: datetime | None = None
    execution_protected_until: datetime | None = None
    expires_at: datetime
    created_at: datetime
    updated_at: datetime


class AdminQuizSignedUrlResponse(QuizContractModel):
    url: str
    expires_at: datetime


class AdminQuizImportReportError(QuizContractModel):
    """One row-level validation error in an import report."""

    row: int | None = Field(default=None, ge=1)
    question_index: int | None = Field(default=None, ge=1)
    field: str | None = Field(default=None, min_length=1, max_length=128)
    error_code: str | None = Field(default=None, min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=1024)


class AdminQuizImportReportResponse(QuizContractModel):
    job_id: int = Field(ge=1)
    errors: list[AdminQuizImportReportError]


class AdminQuizImportErrorQuery(QuizContractModel):
    field: str | None = Field(default=None, min_length=1, max_length=128)
    page: int = Field(default=1, ge=1)


class AdminQuizImportErrorPage(QuizContractModel):
    items: list[AdminQuizImportReportError]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: Literal[50] = 50
    available_fields: list[str] = Field(default_factory=list)
    validation_version: int = Field(ge=0)


class AdminQuizImportCategoryImpactNode(QuizContractModel):
    name: str = Field(min_length=1, max_length=128)
    path: list[str] = Field(min_length=1, max_length=3)
    depth: int = Field(ge=1, le=3)
    status: Literal["existing", "will_create", "blocked"]
    category_id: int | None = None
    direct_question_count: int = Field(ge=0, le=5000)
    subtree_question_count: int = Field(ge=0, le=5000)
    blocking_reasons: list[str] = Field(default_factory=list)
    children: list["AdminQuizImportCategoryImpactNode"] = Field(default_factory=list)


class AdminQuizImportCategoryImpactResponse(QuizContractModel):
    job_id: int = Field(ge=1)
    status: QuizImportStatus
    tree: list[AdminQuizImportCategoryImpactNode]
    new_category_count: int = Field(ge=0, le=500)
    reused_category_count: int = Field(ge=0)
    affected_question_count: int = Field(ge=0, le=5000)
    blocking_reasons: list[str] = Field(default_factory=list)
    lock_version: int = Field(ge=1)
    impact_version: str = Field(min_length=64, max_length=64)
    calculated_at: datetime


class AdminQuizImportConfirmCategoriesRequest(QuizContractModel):
    lock_version: int = Field(ge=1)
    impact_version: str = Field(pattern=r"^[0-9a-f]{64}$")


class AdminQuizImportCancelRequest(QuizContractModel):
    lock_version: int = Field(ge=1)


class AdminQuizAuditQuery(QuizContractModel):
    admin_id: int | None = Field(default=None, ge=1)
    action: str | None = Field(default=None, min_length=1, max_length=64)
    object_type: str | None = Field(default=None, min_length=1, max_length=64)
    object_id: int | None = Field(default=None, ge=1)
    result: Literal["succeeded", "failed"] | None = None
    request_id: str | None = Field(default=None, min_length=1, max_length=64)
    start_at: datetime | None = None
    end_at: datetime | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

    @field_validator("start_at", "end_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("audit time filters must include timezone")
        return value

    @model_validator(mode="after")
    def validate_time_range(self) -> "AdminQuizAuditQuery":
        if self.start_at is not None and self.end_at is not None:
            if self.start_at.astimezone(timezone.utc) > self.end_at.astimezone(timezone.utc):
                raise ValueError("start_at must be earlier than or equal to end_at")
        return self


class AdminQuizAuditFieldChange(QuizContractModel):
    before: JsonValue | None = None
    after: JsonValue | None = None


class AdminQuizAuditLogResponse(QuizContractModel):
    id: int
    actor_type: Literal["admin", "system"]
    admin_id: int | None = None
    permission: str | None = None
    request_id: str | None = None
    ip_address: str | None = None
    action: str
    object_type: str
    object_id: int | None = None
    result: Literal["succeeded", "failed"]
    changed_fields: dict[str, AdminQuizAuditFieldChange] | None = None
    target_ids: list[int] | None = None
    error_summary: str | None = None
    created_at: datetime


# ── Fixed hierarchy V2 ──


class AdminQuizLibraryQuery(QuizContractModel):
    status: QuizLibraryStatus | None = None
    access_mode: QuizLibraryAccessMode | None = None
    keyword: str | None = Field(default=None, min_length=1, max_length=128)
    include_deleted: bool = False


class AdminQuizLibraryCreate(QuizContractModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    cover_url: str | None = Field(default=None, max_length=512)
    details: str | None = Field(default=None, max_length=10000)
    access_mode: QuizLibraryAccessMode = QuizLibraryAccessMode.PENDING
    sort_order: int = 0

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return normalize_category_name(value)


class AdminQuizLibraryUpdate(QuizContractModel):
    lock_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    cover_url: str | None = Field(default=None, max_length=512)
    details: str | None = Field(default=None, max_length=10000)
    access_mode: QuizLibraryAccessMode | None = None
    v2_enabled: bool | None = None
    sort_order: int | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        return normalize_category_name(value) if value is not None else None

    @model_validator(mode="after")
    def require_change(self) -> "AdminQuizLibraryUpdate":
        if not (self.model_fields_set - {"lock_version"}):
            raise ValueError("at least one library field must be supplied")
        return self


class AdminQuizLibraryStatusUpdate(QuizContractModel):
    action: Literal[
        "publish",
        "suspend",
        "restore",
        "archive",
        "delete",
        "undo_delete",
        "reconcile_migration",
    ]
    lock_version: int = Field(ge=1)


class AdminQuizLibraryAccessModeConvert(QuizContractModel):
    lock_version: int = Field(ge=1)
    target_mode: QuizLibraryAccessMode


class AdminQuizLibraryAccessModeConvertResponse(QuizContractModel):
    library: AdminQuizLibraryResponse
    sessions_affected: int = Field(ge=0)


class AdminQuizCourseBindingCreate(QuizContractModel):
    course_id: int = Field(ge=1)


class AdminQuizCourseOptionResponse(QuizContractModel):
    """Narrow course projection available to quiz administrators."""

    id: int
    title: str
    status: Literal["draft", "published", "offline", "archived"]


class AdminQuizCourseBindingStatusUpdate(QuizContractModel):
    status: Literal["active", "inactive"]
    lock_version: int = Field(ge=1)


class AdminQuizCourseBindingResponse(QuizContractModel):
    id: int
    course_id: int
    library_id: int
    status: Literal["active", "inactive"]
    lock_version: int = Field(ge=1)
    created_by: int | None = None
    updated_by: int | None = None
    created_at: datetime
    updated_at: datetime


class AdminQuizLibraryResponse(QuizContractModel):
    id: int
    library_code: str
    name: str
    normalized_name: str
    description: str | None = None
    cover_url: str | None = None
    details: str | None = None
    access_mode: QuizLibraryAccessMode
    system_kind: Literal["none", "migration_quarantine"]
    migration_state: Literal["pending_review", "needs_organization", "ready"]
    status: QuizLibraryStatus
    v2_enabled: bool
    sort_order: int
    lock_version: int = Field(ge=1)
    published_at: datetime | None = None
    suspended_at: datetime | None = None
    archived_at: datetime | None = None
    deleted_at: datetime | None = None
    restore_until: datetime | None = None
    open_migration_issue_count: int = Field(default=0, ge=0)
    module_count: int = Field(default=0, ge=0)
    knowledge_point_count: int = Field(default=0, ge=0)
    question_count: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime


class AdminQuizModuleCreate(QuizContractModel):
    library_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=256)
    sort_order: int = 0

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return normalize_category_name(value)


class AdminQuizModuleUpdate(QuizContractModel):
    lock_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=256)
    sort_order: int | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        return normalize_category_name(value) if value is not None else None

    @model_validator(mode="after")
    def require_change(self) -> "AdminQuizModuleUpdate":
        if not (self.model_fields_set - {"lock_version"}):
            raise ValueError("at least one module field must be supplied")
        return self


class AdminQuizKnowledgePointCreate(QuizContractModel):
    module_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=256)
    sort_order: int = 0

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return normalize_category_name(value)


class AdminQuizKnowledgePointUpdate(QuizContractModel):
    lock_version: int = Field(ge=1)
    module_id: int | None = Field(default=None, ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=256)
    sort_order: int | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        return normalize_category_name(value) if value is not None else None

    @model_validator(mode="after")
    def require_change(self) -> "AdminQuizKnowledgePointUpdate":
        if not (self.model_fields_set - {"lock_version"}):
            raise ValueError("at least one knowledge point field must be supplied")
        return self


class AdminQuizContentStatusUpdate(QuizContractModel):
    status: Literal[QuizContentStatus.ACTIVE, QuizContentStatus.DISABLED]
    lock_version: int = Field(ge=1)


class AdminQuizKnowledgePointResponse(QuizContractModel):
    id: int
    library_id: int
    module_id: int
    name: str
    normalized_name: str
    description: str | None = None
    status: QuizContentStatus
    system_kind: Literal["none", "uncategorized"]
    sort_order: int
    lock_version: int = Field(ge=1)
    question_count: int = Field(default=0, ge=0)
    disabled_at: datetime | None = None
    deleted_at: datetime | None = None
    restore_until: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AdminQuizModuleResponse(QuizContractModel):
    id: int
    library_id: int
    name: str
    normalized_name: str
    description: str | None = None
    status: QuizContentStatus
    system_kind: Literal["none", "pending_organization"]
    sort_order: int
    lock_version: int = Field(ge=1)
    question_count: int = Field(default=0, ge=0)
    knowledge_points: list[AdminQuizKnowledgePointResponse] = Field(default_factory=list)
    disabled_at: datetime | None = None
    deleted_at: datetime | None = None
    restore_until: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AdminQuizContentTreeResponse(QuizContractModel):
    library_id: int
    modules: list[AdminQuizModuleResponse]


class AdminQuizMigrationIssueResponse(QuizContractModel):
    id: int
    library_id: int
    severity: Literal["warning", "blocking"]
    status: Literal["open", "resolved"]
    issue_code: str
    legacy_object_type: Literal["category", "question"]
    legacy_id: int
    original_path: list[dict[str, JsonValue]]
    resolution: str
    resolved_at: datetime | None = None
    created_at: datetime


class AdminQuizMigrationReportResponse(QuizContractModel):
    generated_at: datetime
    library_count: int = Field(ge=0)
    ready_library_count: int = Field(ge=0)
    pending_library_count: int = Field(ge=0)
    open_blocking_issue_count: int = Field(ge=0)
    mapped_category_count: int = Field(ge=0)
    mapped_question_count: int = Field(ge=0)
    issues: list[AdminQuizMigrationIssueResponse]
