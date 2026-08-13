"""Machine-readable frozen contract for all new quiz endpoints."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from pydantic import BaseModel

from app.schemas.admin_quiz_contract import (
    AdminQuizAuditLogResponse,
    AdminQuizAuditQuery,
    AdminQuizBatchRequest,
    AdminQuizBatchResponse,
    AdminQuizCategoryCreate,
    AdminQuizCategoryImpactQuery,
    AdminQuizCategoryImpactResponse,
    AdminQuizCategoryQuery,
    AdminQuizCategoryResponse,
    AdminQuizCategoryStatusUpdate,
    AdminQuizCategoryUpdate,
    AdminQuizCsvImportMetadata,
    AdminQuizImportJobQuery,
    AdminQuizImportJobResponse,
    AdminQuizImportErrorPage,
    AdminQuizImportErrorQuery,
    AdminQuizImportCategoryImpactResponse,
    AdminQuizImportConfirmCategoriesRequest,
    AdminQuizImportCancelRequest,
    AdminQuizJsonImportRequest,
    AdminQuizQuestionCreate,
    AdminQuizQuestionQuery,
    AdminQuizQuestionResponse,
    AdminQuizQuestionStatsResponse,
    AdminQuizQuestionStatsListItem,
    AdminQuizQuestionUpdate,
    AdminQuizSignedUrlResponse,
    AdminQuizStatsOverviewResponse,
    AdminQuizStatsQuestionQuery,
    AdminQuizVersionRequest,
)
from app.schemas.common import APIResponse, PaginatedData
from app.schemas.quiz_contract import (
    QuizCategoryNode,
    QuizCheckinCalendarQuery,
    QuizCheckinDay,
    QuizCheckinStatusResponse,
    QuizCollectionCreate,
    QuizCollectionItem,
    QuizCollectionMutationResponse,
    QuizExamActionResponse,
    QuizExamAnswerSave,
    QuizExamAnswerSaved,
    QuizExamCreate,
    QuizExamDetailResponse,
    QuizExamListItem,
    QuizExamListQuery,
    QuizPracticeAbandonResponse,
    QuizPracticeAttemptCreate,
    QuizPracticeAttemptResult,
    QuizPracticeHistoryItem,
    QuizPracticeHistoryQuery,
    QuizPracticeSessionCreate,
    QuizPracticeSessionResponse,
    QuizPublicQuestion,
    QuizQuestionListQuery,
    QuizStatsResponse,
    QuizWrongBookItem,
    QuizWrongBookQuery,
)


QUIZ_CONTRACT_VERSION = "2026-08-13"


class QuizErrorCode(IntEnum):
    VALIDATION = 40001
    UNAUTHORIZED = 40100
    FORBIDDEN = 40101
    BUSINESS_RULE = 40200
    VERSION_CONFLICT = 40201
    RATE_LIMITED = 40202
    NOT_FOUND = 40300
    INTERNAL_ERROR = 50000


class QuizErrorResponse(BaseModel):
    code: QuizErrorCode
    message: str
    data: None = None
    detail: list[dict[str, str]] | None = None


@dataclass(frozen=True, slots=True)
class QuizEndpointContract:
    method: str
    path: str
    auth: str
    response_model: object
    query_model: object | None = None
    body_model: object | None = None
    permission: str | None = None
    rate_limit_per_minute: int | None = None
    errors: tuple[QuizErrorCode, ...] = (
        QuizErrorCode.VALIDATION,
        QuizErrorCode.UNAUTHORIZED,
        QuizErrorCode.FORBIDDEN,
        QuizErrorCode.BUSINESS_RULE,
        QuizErrorCode.NOT_FOUND,
        QuizErrorCode.INTERNAL_ERROR,
    )
    example: dict[str, object] = field(
        default_factory=lambda: {
            "response": {"code": 0, "message": "ok", "data": None}
        }
    )


_CONFLICT_ERRORS = (
    QuizErrorCode.VALIDATION,
    QuizErrorCode.UNAUTHORIZED,
    QuizErrorCode.FORBIDDEN,
    QuizErrorCode.BUSINESS_RULE,
    QuizErrorCode.VERSION_CONFLICT,
    QuizErrorCode.NOT_FOUND,
    QuizErrorCode.INTERNAL_ERROR,
)
_RATE_ERRORS = (*_CONFLICT_ERRORS, QuizErrorCode.RATE_LIMITED)


QUIZ_API_CONTRACTS: tuple[QuizEndpointContract, ...] = (
    QuizEndpointContract(
        "GET", "/api/quiz/categories", "public", APIResponse[list[QuizCategoryNode]]
    ),
    QuizEndpointContract(
        "GET",
        "/api/quiz/questions",
        "user",
        APIResponse[PaginatedData[QuizPublicQuestion]],
        query_model=QuizQuestionListQuery,
        rate_limit_per_minute=60,
        errors=_RATE_ERRORS,
    ),
    QuizEndpointContract(
        "POST",
        "/api/quiz/practice-sessions",
        "user",
        APIResponse[QuizPracticeSessionResponse],
        body_model=QuizPracticeSessionCreate,
        errors=_CONFLICT_ERRORS,
        example={
            "request": {"mode": "normal", "category_id": 1, "question_count": 20}
        },
    ),
    QuizEndpointContract(
        "GET",
        "/api/quiz/practice-sessions/current",
        "user",
        APIResponse[QuizPracticeSessionResponse | None],
    ),
    QuizEndpointContract(
        "GET",
        "/api/quiz/practice-sessions/{session_id}",
        "user",
        APIResponse[QuizPracticeSessionResponse],
    ),
    QuizEndpointContract(
        "POST",
        "/api/quiz/practice-sessions/{session_id}/attempts",
        "user",
        APIResponse[QuizPracticeAttemptResult],
        body_model=QuizPracticeAttemptCreate,
        rate_limit_per_minute=120,
        errors=_RATE_ERRORS,
        example={
            "request": {
                "session_question_id": 9,
                "idempotency_key": "f31f92c2-1",
                "user_answer": ["A", "C"],
            }
        },
    ),
    QuizEndpointContract(
        "POST",
        "/api/quiz/practice-sessions/{session_id}/abandon",
        "user",
        APIResponse[QuizPracticeAbandonResponse],
        errors=_CONFLICT_ERRORS,
    ),
    QuizEndpointContract(
        "GET",
        "/api/quiz/practice-history",
        "user",
        APIResponse[PaginatedData[QuizPracticeHistoryItem]],
        query_model=QuizPracticeHistoryQuery,
    ),
    QuizEndpointContract(
        "GET",
        "/api/quiz/wrong-book",
        "user",
        APIResponse[PaginatedData[QuizWrongBookItem]],
        query_model=QuizWrongBookQuery,
    ),
    QuizEndpointContract(
        "GET",
        "/api/quiz/collections",
        "user",
        APIResponse[PaginatedData[QuizCollectionItem]],
        query_model=QuizWrongBookQuery,
    ),
    QuizEndpointContract(
        "POST",
        "/api/quiz/collections",
        "user",
        APIResponse[QuizCollectionMutationResponse],
        body_model=QuizCollectionCreate,
    ),
    QuizEndpointContract(
        "DELETE",
        "/api/quiz/collections/{question_id}",
        "user",
        APIResponse[QuizCollectionMutationResponse],
    ),
    QuizEndpointContract(
        "GET",
        "/api/quiz/checkin",
        "user",
        APIResponse[QuizCheckinStatusResponse],
    ),
    QuizEndpointContract(
        "GET",
        "/api/quiz/checkin/calendar",
        "user",
        APIResponse[list[QuizCheckinDay]],
        query_model=QuizCheckinCalendarQuery,
    ),
    QuizEndpointContract(
        "GET", "/api/quiz/stats", "user", APIResponse[QuizStatsResponse]
    ),
    QuizEndpointContract(
        "POST",
        "/api/quiz/exams",
        "user",
        APIResponse[QuizExamDetailResponse],
        body_model=QuizExamCreate,
        errors=_CONFLICT_ERRORS,
        example={"request": {"category_id": 1, "question_count": 50}},
    ),
    QuizEndpointContract(
        "GET",
        "/api/quiz/exams/current",
        "user",
        APIResponse[QuizExamDetailResponse | None],
    ),
    QuizEndpointContract(
        "GET",
        "/api/quiz/exams",
        "user",
        APIResponse[PaginatedData[QuizExamListItem]],
        query_model=QuizExamListQuery,
    ),
    QuizEndpointContract(
        "GET",
        "/api/quiz/exams/{exam_id}",
        "user",
        APIResponse[QuizExamDetailResponse],
    ),
    QuizEndpointContract(
        "PUT",
        "/api/quiz/exams/{exam_id}/answers/{exam_question_id}",
        "user",
        APIResponse[QuizExamAnswerSaved],
        body_model=QuizExamAnswerSave,
        rate_limit_per_minute=120,
        errors=_RATE_ERRORS,
        example={"request": {"user_answer": "A", "lock_version": 0}},
    ),
    QuizEndpointContract(
        "POST",
        "/api/quiz/exams/{exam_id}/submit",
        "user",
        APIResponse[QuizExamActionResponse],
        errors=_CONFLICT_ERRORS,
    ),
    QuizEndpointContract(
        "POST",
        "/api/quiz/exams/{exam_id}/abandon",
        "user",
        APIResponse[QuizExamActionResponse],
        errors=_CONFLICT_ERRORS,
    ),
    QuizEndpointContract(
        "GET",
        "/admin/quiz/categories",
        "admin",
        APIResponse[list[AdminQuizCategoryResponse]],
        query_model=AdminQuizCategoryQuery,
        permission="quiz:list",
    ),
    QuizEndpointContract(
        "POST",
        "/admin/quiz/categories",
        "admin",
        APIResponse[AdminQuizCategoryResponse],
        body_model=AdminQuizCategoryCreate,
        permission="quiz:write",
        rate_limit_per_minute=120,
        errors=_RATE_ERRORS,
    ),
    QuizEndpointContract(
        "PUT",
        "/admin/quiz/categories/{category_id}",
        "admin",
        APIResponse[AdminQuizCategoryResponse],
        body_model=AdminQuizCategoryUpdate,
        permission="quiz:write",
        rate_limit_per_minute=120,
        errors=_RATE_ERRORS,
    ),
    QuizEndpointContract(
        "DELETE",
        "/admin/quiz/categories/{category_id}",
        "admin",
        APIResponse[None],
        body_model=AdminQuizVersionRequest,
        permission="quiz:write",
        rate_limit_per_minute=120,
        errors=_RATE_ERRORS,
    ),
    QuizEndpointContract(
        "POST",
        "/admin/quiz/categories/{category_id}/status",
        "admin",
        APIResponse[AdminQuizCategoryResponse],
        body_model=AdminQuizCategoryStatusUpdate,
        permission="quiz:write",
        rate_limit_per_minute=120,
        errors=_RATE_ERRORS,
    ),
    QuizEndpointContract(
        "GET",
        "/admin/quiz/categories/{category_id}/impact",
        "admin",
        APIResponse[AdminQuizCategoryImpactResponse],
        query_model=AdminQuizCategoryImpactQuery,
        permission="quiz:write",
        rate_limit_per_minute=120,
        errors=_RATE_ERRORS,
    ),
    QuizEndpointContract(
        "GET",
        "/admin/quiz/questions",
        "admin",
        APIResponse[PaginatedData[AdminQuizQuestionResponse]],
        query_model=AdminQuizQuestionQuery,
        permission="quiz:list",
    ),
    QuizEndpointContract(
        "POST",
        "/admin/quiz/questions",
        "admin",
        APIResponse[AdminQuizQuestionResponse],
        body_model=AdminQuizQuestionCreate,
        permission="quiz:write",
        rate_limit_per_minute=120,
        errors=_RATE_ERRORS,
    ),
    QuizEndpointContract(
        "PUT",
        "/admin/quiz/questions/{question_id}",
        "admin",
        APIResponse[AdminQuizQuestionResponse],
        body_model=AdminQuizQuestionUpdate,
        permission="quiz:write",
        rate_limit_per_minute=120,
        errors=_RATE_ERRORS,
    ),
    QuizEndpointContract(
        "DELETE",
        "/admin/quiz/questions/{question_id}",
        "admin",
        APIResponse[None],
        body_model=AdminQuizVersionRequest,
        permission="quiz:write",
        rate_limit_per_minute=120,
        errors=_RATE_ERRORS,
    ),
    QuizEndpointContract(
        "POST",
        "/admin/quiz/questions/{question_id}/publish",
        "admin",
        APIResponse[AdminQuizQuestionResponse],
        body_model=AdminQuizVersionRequest,
        permission="quiz:write",
        rate_limit_per_minute=120,
        errors=_RATE_ERRORS,
    ),
    QuizEndpointContract(
        "POST",
        "/admin/quiz/questions/{question_id}/disable",
        "admin",
        APIResponse[AdminQuizQuestionResponse],
        body_model=AdminQuizVersionRequest,
        permission="quiz:write",
        rate_limit_per_minute=120,
        errors=_RATE_ERRORS,
    ),
    QuizEndpointContract(
        "POST",
        "/admin/quiz/questions/{question_id}/restore",
        "admin",
        APIResponse[AdminQuizQuestionResponse],
        body_model=AdminQuizVersionRequest,
        permission="quiz:write",
        rate_limit_per_minute=120,
        errors=_RATE_ERRORS,
    ),
    QuizEndpointContract(
        "POST",
        "/admin/quiz/questions/batch-publish",
        "admin",
        APIResponse[AdminQuizBatchResponse],
        body_model=AdminQuizBatchRequest,
        permission="quiz:write",
        rate_limit_per_minute=30,
        errors=_RATE_ERRORS,
    ),
    QuizEndpointContract(
        "POST",
        "/admin/quiz/questions/batch-disable",
        "admin",
        APIResponse[AdminQuizBatchResponse],
        body_model=AdminQuizBatchRequest,
        permission="quiz:write",
        rate_limit_per_minute=30,
        errors=_RATE_ERRORS,
    ),
    QuizEndpointContract(
        "GET",
        "/admin/quiz/questions/{question_id}/stats",
        "admin",
        APIResponse[AdminQuizQuestionStatsResponse],
        permission="quiz:list",
    ),
    QuizEndpointContract(
        "POST",
        "/admin/quiz/imports/csv",
        "admin",
        APIResponse[AdminQuizImportJobResponse],
        body_model=AdminQuizCsvImportMetadata,
        permission="quiz:import",
        rate_limit_per_minute=10,
        errors=_RATE_ERRORS,
    ),
    QuizEndpointContract(
        "POST",
        "/admin/quiz/imports/json",
        "admin",
        APIResponse[AdminQuizImportJobResponse],
        body_model=AdminQuizJsonImportRequest,
        permission="quiz:import",
        rate_limit_per_minute=10,
        errors=_RATE_ERRORS,
    ),
    QuizEndpointContract(
        "GET",
        "/admin/quiz/imports",
        "admin",
        APIResponse[PaginatedData[AdminQuizImportJobResponse]],
        query_model=AdminQuizImportJobQuery,
        permission="quiz:list",
    ),
    QuizEndpointContract(
        "GET",
        "/admin/quiz/imports/{job_id}/source-url",
        "admin",
        APIResponse[AdminQuizSignedUrlResponse],
        permission="quiz:list",
        rate_limit_per_minute=60,
        errors=_RATE_ERRORS,
    ),
    QuizEndpointContract(
        "GET",
        "/admin/quiz/imports/{job_id}/errors",
        "admin",
        APIResponse[AdminQuizImportErrorPage],
        query_model=AdminQuizImportErrorQuery,
        permission="quiz:list",
    ),
    QuizEndpointContract(
        "GET",
        "/admin/quiz/imports/{job_id}/category-impact",
        "admin",
        APIResponse[AdminQuizImportCategoryImpactResponse],
        permission="quiz:list",
    ),
    QuizEndpointContract(
        "POST",
        "/admin/quiz/imports/{job_id}/confirm-categories",
        "admin",
        APIResponse[AdminQuizImportJobResponse],
        body_model=AdminQuizImportConfirmCategoriesRequest,
        permission="quiz:import+quiz:write",
        rate_limit_per_minute=10,
        errors=_RATE_ERRORS,
    ),
    QuizEndpointContract(
        "POST",
        "/admin/quiz/imports/{job_id}/cancel",
        "admin",
        APIResponse[AdminQuizImportJobResponse],
        body_model=AdminQuizImportCancelRequest,
        permission="quiz:import",
        rate_limit_per_minute=10,
        errors=_RATE_ERRORS,
    ),
    QuizEndpointContract(
        "POST",
        "/admin/quiz/imports/{job_id}/retry",
        "admin",
        APIResponse[AdminQuizImportJobResponse],
        permission="quiz:import",
        rate_limit_per_minute=10,
        errors=_RATE_ERRORS,
    ),
    QuizEndpointContract(
        "GET",
        "/admin/quiz/stats/overview",
        "admin",
        APIResponse[AdminQuizStatsOverviewResponse],
        permission="quiz:list",
    ),
    QuizEndpointContract(
        "GET",
        "/admin/quiz/stats/questions",
        "admin",
        APIResponse[PaginatedData[AdminQuizQuestionStatsListItem]],
        query_model=AdminQuizStatsQuestionQuery,
        permission="quiz:list",
    ),
    QuizEndpointContract(
        "GET",
        "/admin/quiz/imports/{job_id}",
        "admin",
        APIResponse[AdminQuizImportJobResponse],
        permission="quiz:list",
    ),
    QuizEndpointContract(
        "GET",
        "/admin/quiz/imports/{job_id}/report-url",
        "admin",
        APIResponse[AdminQuizSignedUrlResponse],
        permission="quiz:list",
        rate_limit_per_minute=60,
        errors=_RATE_ERRORS,
    ),
    QuizEndpointContract(
        "GET",
        "/admin/quiz/audit-logs",
        "admin",
        APIResponse[PaginatedData[AdminQuizAuditLogResponse]],
        query_model=AdminQuizAuditQuery,
        permission="quiz:list",
    ),
)


DELETED_QUIZ_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("POST", "/api/quiz/submit"),
    ("POST", "/api/quiz/wrong-book"),
    ("DELETE", "/api/quiz/wrong-book/{id}"),
    ("POST", "/api/quiz/checkin"),
    ("POST", "/api/quiz/exam/start"),
    ("POST", "/api/quiz/exam/submit"),
    ("GET", "/api/quiz/exam/history"),
    ("GET", "/api/quiz/exam/current"),
    ("GET", "/api/quiz/exam/{exam_id}"),
    ("GET", "/api/quiz/progress"),
    ("GET", "/api/quiz/recent"),
    ("POST", "/admin/quiz/questions/batch-delete"),
    ("POST", "/admin/quiz/import"),
    ("POST", "/admin/quiz/import/json"),
)


def _validate_contract_registry() -> None:
    keys = [(entry.method, entry.path) for entry in QUIZ_API_CONTRACTS]
    if len(keys) != len(set(keys)):
        raise RuntimeError("duplicate quiz endpoint contract")
    for entry in QUIZ_API_CONTRACTS:
        if entry.auth == "admin" and not entry.permission:
            raise RuntimeError(f"admin quiz endpoint lacks permission: {entry.path}")
        for model in (entry.query_model, entry.body_model):
            if model is not None and not isinstance(model, type):
                raise RuntimeError(f"invalid quiz request model: {entry.method} {entry.path}")


_validate_contract_registry()
