"""Admin request and response models for exam essay review."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from app.schemas.quiz_contract import QuizContractModel


AdminQuizReviewVerdict = Literal["wrong", "partial", "correct"]
AdminQuizReviewStatus = Literal["pending", "in_progress", "recalled", "completed"]


class AdminQuizReviewListQuery(QuizContractModel):
    status: Literal["pending", "in_progress", "recalled"] | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class AdminQuizReviewListItem(QuizContractModel):
    exam_id: int
    user_id: int
    status: Literal["completed", "timed_out"]
    review_status: Literal["pending", "in_progress", "recalled"]
    review_locked_by: int | None = None
    question_count: int = Field(ge=10, le=100)
    submitted_at: datetime | None = None
    timed_out_at: datetime | None = None


class AdminQuizReviewQuestion(QuizContractModel):
    exam_question_id: int
    position: int = Field(ge=1)
    question_text: str
    reference_answer: str
    explanation: str
    image_urls: list[str] = Field(default_factory=list)
    user_answer: str | None = None
    answered: bool
    verdict: AdminQuizReviewVerdict | None = None
    comment: str | None = Field(default=None, max_length=512)


class AdminQuizReviewDetail(QuizContractModel):
    exam_id: int
    user_id: int
    status: Literal["completed", "timed_out"]
    review_status: Literal["pending", "in_progress", "recalled"]
    question_count: int = Field(ge=10, le=100)
    submitted_at: datetime | None = None
    timed_out_at: datetime | None = None
    questions: list[AdminQuizReviewQuestion]


class AdminQuizReviewVerdictItem(QuizContractModel):
    exam_question_id: int
    verdict: AdminQuizReviewVerdict
    comment: str | None = Field(default=None, max_length=512)


class AdminQuizReviewSubmitRequest(QuizContractModel):
    verdicts: list[AdminQuizReviewVerdictItem] = Field(min_length=1, max_length=100)


class AdminQuizReviewClaimResponse(QuizContractModel):
    exam_id: int
    review_status: AdminQuizReviewStatus
    review_locked_by: int | None = None


class AdminQuizReviewCompleteResponse(QuizContractModel):
    exam_id: int
    review_status: Literal["completed"]
    score: Decimal = Field(ge=0, le=100, decimal_places=1)
    correct_count: int = Field(ge=0)
    partial_count: int = Field(ge=0)
    wrong_count: int = Field(ge=0)
    unanswered_count: int = Field(ge=0)
