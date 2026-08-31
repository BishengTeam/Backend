"""Contracts for course-bound assignments and manual essay review."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.quiz_contract import QuizAnswer, QuizContractModel


CourseAssignmentStatus = Literal["draft", "published", "disabled"]
CourseAssignmentSubmissionStatus = Literal[
    "draft", "submitted", "claimed", "graded"
]
CourseAssignmentQuestionType = Literal[
    "single_choice", "multiple_choice", "judge", "essay"
]
CourseAssignmentReviewAction = Literal["save", "claim", "complete", "reopen"]


class CourseAssignmentEssayScoreInput(QuizContractModel):
    question_id: int = Field(ge=1)
    score: Decimal = Field(gt=0, le=100, decimal_places=1)


class CourseAssignmentCreate(QuizContractModel):
    course_id: int = Field(ge=1)
    library_id: int = Field(ge=1)
    essay_scores: list[CourseAssignmentEssayScoreInput] = Field(
        default_factory=list, max_length=500
    )

    @field_validator("essay_scores")
    @classmethod
    def reject_duplicate_questions(
        cls, value: list[CourseAssignmentEssayScoreInput]
    ) -> list[CourseAssignmentEssayScoreInput]:
        ids = [item.question_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("essay_scores cannot contain duplicate questions")
        return value


class CourseAssignmentUpdate(QuizContractModel):
    lock_version: int = Field(ge=1)
    essay_scores: list[CourseAssignmentEssayScoreInput] | None = Field(
        default=None, max_length=500
    )

    @field_validator("essay_scores")
    @classmethod
    def reject_duplicate_questions(
        cls, value: list[CourseAssignmentEssayScoreInput] | None
    ) -> list[CourseAssignmentEssayScoreInput] | None:
        if value is None:
            return value
        ids = [item.question_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("essay_scores cannot contain duplicate questions")
        return value


class CourseAssignmentAction(QuizContractModel):
    lock_version: int = Field(ge=1)


class CourseAssignmentQuestionResponse(QuizContractModel):
    question_id: int
    position: int = Field(ge=1)
    question_type: CourseAssignmentQuestionType
    question_text: str
    options: dict[str, str] | None = None
    option_image_urls: dict[str, str] = Field(default_factory=dict)
    image_urls: list[str] = Field(default_factory=list)
    explanation: str | None = None
    reference_answer: str | None = None
    score: Decimal = Field(ge=0, le=100)
    is_essay: bool


class CourseAssignmentResponse(QuizContractModel):
    id: int
    library_id: int
    library_name: str
    library_code: str
    course_ids: list[int] = Field(default_factory=list)
    status: CourseAssignmentStatus
    version_no: int = Field(ge=1)
    question_count: int = Field(ge=0)
    essay_count: int = Field(ge=0)
    objective_count: int = Field(ge=0)
    essay_total_score: Decimal
    objective_total_score: Decimal
    total_score: Decimal
    published_at: datetime | None = None
    disabled_at: datetime | None = None
    lock_version: int = Field(ge=1)
    questions: list[CourseAssignmentQuestionResponse] = Field(default_factory=list)


class UserCourseAssignmentListItem(QuizContractModel):
    assignment_id: int
    library_id: int
    library_name: str
    library_code: str
    description: str = ""
    cover_url: str = ""
    question_count: int = Field(ge=1)
    essay_count: int = Field(ge=0)
    status: CourseAssignmentSubmissionStatus | None = None
    display_status: Literal[
        "not_started", "draft", "submitted", "reviewing", "graded"
    ]
    can_withdraw: bool = False
    total_score: Decimal | None = None
    submitted_at: datetime | None = None
    graded_at: datetime | None = None


class UserCourseAssignmentQuestion(QuizContractModel):
    question_id: int
    position: int = Field(ge=1)
    question_type: CourseAssignmentQuestionType
    question_text: str
    options: dict[str, str] = Field(default_factory=dict)
    option_image_urls: dict[str, str] = Field(default_factory=dict)
    image_urls: list[str] = Field(default_factory=list)
    score: Decimal
    is_essay: bool
    user_answer: QuizAnswer | None = None
    is_answered: bool = False


class UserCourseAssignmentResultQuestion(UserCourseAssignmentQuestion):
    correct_answer: QuizAnswer | None = None
    explanation: str | None = None
    earned_score: Decimal | None = None
    manual_score: Decimal | None = None
    review_comment: str | None = None
    requires_review: bool = False


class UserCourseAssignmentDetail(QuizContractModel):
    assignment_id: int
    library_id: int
    library_name: str
    status: CourseAssignmentSubmissionStatus | None = None
    display_status: Literal[
        "not_started", "draft", "submitted", "reviewing", "graded"
    ]
    assignment_status: CourseAssignmentStatus
    config_version_no: int = Field(ge=1)
    can_edit: bool
    can_submit: bool
    can_withdraw: bool
    result_available: bool
    total_score: Decimal | None = None
    submitted_at: datetime | None = None
    graded_at: datetime | None = None
    questions: list[UserCourseAssignmentResultQuestion]


class CourseAssignmentAnswerInput(QuizContractModel):
    question_id: int = Field(ge=1)
    user_answer: QuizAnswer | None = None


class CourseAssignmentAnswerSave(QuizContractModel):
    answers: list[CourseAssignmentAnswerInput] = Field(
        min_length=1, max_length=500
    )

    @field_validator("answers")
    @classmethod
    def reject_duplicates(
        cls, value: list[CourseAssignmentAnswerInput]
    ) -> list[CourseAssignmentAnswerInput]:
        ids = [item.question_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("answers cannot contain duplicate questions")
        return value


class CourseAssignmentAnswerSaved(QuizContractModel):
    assignment_id: int
    saved_count: int = Field(ge=1)
    config_version_no: int = Field(ge=1)


class CourseAssignmentSubmitResponse(QuizContractModel):
    assignment_id: int
    status: CourseAssignmentSubmissionStatus
    display_status: Literal[
        "not_started", "draft", "submitted", "reviewing", "graded"
    ]
    total_score: Decimal | None = None
    submitted_at: datetime


class CourseAssignmentWithdrawResponse(QuizContractModel):
    assignment_id: int
    status: Literal["draft"]


class CourseAssignmentSubmissionQuery(QuizContractModel):
    course_id: int | None = Field(default=None, ge=1)
    library_id: int | None = Field(default=None, ge=1)
    assignment_id: int | None = Field(default=None, ge=1)
    status: CourseAssignmentSubmissionStatus | None = None
    keyword: str | None = Field(default=None, min_length=1, max_length=128)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class CourseAssignmentSubmissionListItem(QuizContractModel):
    id: int
    assignment_id: int
    library_id: int
    library_name: str
    course_ids: list[int] = Field(default_factory=list)
    user_id: int
    student_name: str
    student_phone: str | None = None
    status: CourseAssignmentSubmissionStatus
    config_version_no: int
    submitted_at: datetime | None = None
    claimed_at: datetime | None = None
    graded_at: datetime | None = None
    total_score: Decimal | None = None
    lock_version: int = Field(ge=1)


class CourseAssignmentReviewQuestionInput(QuizContractModel):
    submission_question_id: int = Field(ge=1)
    score: Decimal = Field(ge=0, le=100, decimal_places=1)
    comment: str | None = Field(default=None, max_length=1000)


class CourseAssignmentReviewSave(QuizContractModel):
    lock_version: int = Field(ge=1)
    complete: bool = False
    scores: list[CourseAssignmentReviewQuestionInput] = Field(
        default_factory=list, max_length=500
    )

    @model_validator(mode="after")
    def validate_action(self) -> "CourseAssignmentReviewSave":
        if self.complete and not self.scores:
            raise ValueError("complete requires at least one score")
        ids = [item.submission_question_id for item in self.scores]
        if len(ids) != len(set(ids)):
            raise ValueError("scores cannot contain duplicate submission questions")
        return self


class AdminCourseAssignmentResultQuestion(UserCourseAssignmentResultQuestion):
    submission_question_id: int
    reference_answer: str | None = None
    is_objective_correct: bool | None = None


class AdminCourseAssignmentSubmissionDetail(QuizContractModel):
    id: int
    assignment_id: int
    library_id: int
    library_name: str
    course_ids: list[int] = Field(default_factory=list)
    user_id: int
    student_name: str
    student_phone: str | None = None
    status: CourseAssignmentSubmissionStatus
    config_version_no: int
    submitted_at: datetime | None = None
    claimed_by: int | None = None
    claimed_at: datetime | None = None
    graded_by: int | None = None
    graded_at: datetime | None = None
    total_score: Decimal | None = None
    lock_version: int = Field(ge=1)
    questions: list[AdminCourseAssignmentResultQuestion]


class CourseAssignmentReviewSaved(QuizContractModel):
    submission_id: int
    status: CourseAssignmentSubmissionStatus
    total_score: Decimal | None = None
    lock_version: int = Field(ge=1)


class CourseAssignmentReviewLogItem(QuizContractModel):
    id: int
    action: CourseAssignmentReviewAction
    admin_id: int | None = None
    before: dict[str, object] | None = None
    after: dict[str, object] | None = None
    created_at: datetime
