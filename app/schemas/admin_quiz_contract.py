"""Frozen admin request and response models for the new quiz API."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, JsonValue, field_validator, model_validator

from app.domain.community.src.rule.quiz import (
    QuizCategoryStatus,
    QuizImportSourceType,
    QuizImportStatus,
    QuizQuestionStatus,
    QuizQuestionType,
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


class AdminQuizQuestionQuery(QuizContractModel):
    category_id: int | None = Field(default=None, ge=1)
    question_type: QuizQuestionType | None = None
    status: QuizQuestionStatus | None = None
    keyword: str | None = Field(default=None, min_length=1, max_length=128)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class AdminQuizQuestionCreate(QuizContractModel):
    category_id: int = Field(ge=1)
    question_type: QuizQuestionType
    question_text: str = Field(min_length=1, max_length=1024)
    options: dict[str, str] | None = None
    correct_answer: QuizAnswer | None = None
    explanation: str | None = Field(default=None, max_length=1024)

    @model_validator(mode="after")
    def normalize_draft(self) -> "AdminQuizQuestionCreate":
        normalized = normalize_question_payload(
            question_type=self.question_type,
            question_text=self.question_text,
            options=self.options,
            correct_answer=self.correct_answer,
            explanation=self.explanation,
            require_publishable=False,
        )
        self.question_type = normalized.question_type
        self.question_text = normalized.question_text
        self.options = normalized.options
        self.correct_answer = normalized.correct_answer
        self.explanation = normalized.explanation
        return self


class AdminQuizQuestionUpdate(QuizContractModel):
    lock_version: int = Field(ge=1)
    category_id: int | None = Field(default=None, ge=1)
    question_type: QuizQuestionType | None = None
    question_text: str | None = Field(default=None, min_length=1, max_length=1024)
    options: dict[str, str] | None = None
    correct_answer: QuizAnswer | None = None
    explanation: str | None = Field(default=None, max_length=1024)

    @model_validator(mode="after")
    def require_change(self) -> "AdminQuizQuestionUpdate":
        if not (self.model_fields_set - {"lock_version"}):
            raise ValueError("at least one question field must be supplied")
        return self


class AdminQuizQuestionResponse(QuizContractModel):
    id: int
    category_id: int
    question_type: QuizQuestionType
    status: QuizQuestionStatus
    question_text: str
    normalized_question_text: str
    options: dict[str, str] | None = None
    correct_answer: QuizAnswer | None = None
    explanation: str | None = None
    ever_published: bool
    published_at: datetime | None = None
    disabled_at: datetime | None = None
    lock_version: int = Field(ge=1)
    created_by: int
    updated_by: int
    created_at: datetime
    updated_at: datetime


class AdminQuizVersionRequest(QuizContractModel):
    lock_version: int = Field(ge=1)


class AdminQuizBatchTarget(QuizContractModel):
    question_id: int = Field(ge=1)
    lock_version: int = Field(ge=1)


class AdminQuizBatchRequest(QuizContractModel):
    items: list[AdminQuizBatchTarget] = Field(min_length=1, max_length=5000)

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
            require_publishable=False,
        )
        self.question_type = normalized.question_type
        self.question_text = normalized.question_text
        self.options = normalized.options
        self.correct_answer = normalized.correct_answer
        self.explanation = normalized.explanation
        return self


class AdminQuizJsonImportRequest(QuizContractModel):
    questions: list[AdminQuizImportQuestion] = Field(min_length=1, max_length=5000)


class AdminQuizImportJobQuery(QuizContractModel):
    status: QuizImportStatus | None = None
    source_type: QuizImportSourceType | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class AdminQuizImportJobResponse(QuizContractModel):
    id: int
    admin_id: int | None = None
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
    expires_at: datetime
    created_at: datetime
    updated_at: datetime


class AdminQuizSignedUrlResponse(QuizContractModel):
    url: str
    expires_at: datetime


class AdminQuizImportReportError(QuizContractModel):
    """One row-level validation error in an import report."""

    row: int | None = Field(default=None, ge=1)
    field: str | None = Field(default=None, min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=1024)


class AdminQuizImportReportResponse(QuizContractModel):
    job_id: int = Field(ge=1)
    errors: list[AdminQuizImportReportError]


class AdminQuizAuditQuery(QuizContractModel):
    admin_id: int | None = Field(default=None, ge=1)
    action: str | None = Field(default=None, min_length=1, max_length=64)
    object_type: str | None = Field(default=None, min_length=1, max_length=64)
    object_id: int | None = Field(default=None, ge=1)
    result: Literal["succeeded", "failed"] | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


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
