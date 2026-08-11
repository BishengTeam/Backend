"""Legacy quiz schemas retained for migration/test import compatibility only.

The active HTTP contract lives in :mod:`app.schemas.quiz_contract`; no route
uses the request/response models below after QB-31. They must not be used to
add aliases for deleted endpoints.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


QuizQuestionType = Literal["single_choice", "multiple_choice", "judge"]


class QuizCategoryResponse(BaseModel):
    id: int
    name: str = Field(..., description="分类名称")
    parent_id: int | None = Field(None, description="父级分类 ID")
    description: str | None = Field(None, description="分类描述")
    question_count: int = Field(0, description="题目数量（含子分类）")

    model_config = {"from_attributes": True}


class QuizCategoryTreeResponse(QuizCategoryResponse):
    children: list[QuizCategoryTreeResponse] = Field(default_factory=list, description="子分类列表")


class QuizQuestionQuery(BaseModel):
    category_id: int | None = Field(None, ge=1)
    question_type: QuizQuestionType | None = Field(None)
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)


class QuizQuestionResponse(BaseModel):
    id: int
    category_id: int = Field(..., description="所属分类 ID")
    question_type: QuizQuestionType = Field(..., description="题型")
    question_text: str = Field(..., description="题干")
    options: dict[str, Any] | None = Field(None, description="选项 JSON")
    explanation: str | None = Field(None, description="题目解析")

    model_config = {"from_attributes": True}


class QuizSubmitRequest(BaseModel):
    question_id: int = Field(..., ge=1)
    user_answer: str = Field(..., min_length=1, max_length=256)


class QuizSubmitResponse(BaseModel):
    record_id: int = Field(..., description="答题记录 ID")
    question_id: int = Field(..., description="题目 ID")
    user_answer: str = Field(..., description="用户答案")
    is_correct: bool = Field(..., description="是否回答正确")
    is_wrong: bool = Field(..., description="是否进入错题本")
    correct_answer: str = Field(..., description="标准答案")
    explanation: str | None = Field(None, description="题目解析")


class QuizRecordQuestionResponse(BaseModel):
    id: int = Field(..., description="答题记录 ID")
    question_id: int = Field(..., description="题目 ID")
    user_answer: str | None = Field(None)
    is_correct: bool | None = Field(None)
    is_wrong: bool = Field(..., description="是否在错题本")
    is_collected: bool = Field(..., description="是否已收藏")
    question: QuizQuestionResponse = Field(..., description="题目信息")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="更新时间")

    model_config = {"from_attributes": True}


class QuizToggleRequest(BaseModel):
    question_id: int = Field(..., ge=1)


class QuizToggleResponse(BaseModel):
    id: int = Field(..., description="答题记录 ID")
    question_id: int = Field(..., description="题目 ID")
    is_wrong: bool = Field(..., description="是否在错题本")
    is_collected: bool = Field(..., description="是否已收藏")
    updated_at: datetime = Field(..., description="更新时间")

    model_config = {"from_attributes": True}


QuizWrongBookRequest = QuizToggleRequest
QuizWrongBookResponse = QuizToggleResponse
QuizCollectionRequest = QuizToggleRequest
QuizCollectionResponse = QuizToggleResponse


class QuizCheckinRequest(BaseModel):
    questions_completed: int = Field(0, ge=0, description="当天完成题数")


class QuizCheckinResponse(BaseModel):
    id: int | None = Field(None, description="打卡记录 ID")
    checkin_date: date = Field(..., description="打卡日期")
    checked_in: bool = Field(..., description="当天是否已打卡")
    questions_completed: int = Field(..., ge=0, description="当天完成题数")
    consecutive_days: int = Field(..., ge=0, description="连续打卡天数")

    model_config = {"from_attributes": True}


# ── 练习统计 ──────────────────────────────────────────────────

class QuizStatsResponse(BaseModel):
    total_answers: int = Field(0)
    correct_answers: int = Field(0)
    accuracy: float = Field(0.0)
    total_questions: int = Field(0)
    answered_questions: int = Field(0)
    completion_rate: float = Field(0.0)
    streak_days: int = Field(0)
    total_checkin_days: int = Field(0)
    wrong_count: int = Field(0)
    collected_count: int = Field(0)
    today_answers: int = Field(0)
    today_correct: int = Field(0)


# ── 模拟考试 ──────────────────────────────────────────────────

class QuizExamStartRequest(BaseModel):
    category_id: int | None = Field(None, ge=1)
    question_count: int = Field(..., ge=1, le=200)
    duration_minutes: int = Field(60, ge=1, le=180)
    question_type: QuizQuestionType | None = Field(None)


class QuizExamStartResponse(BaseModel):
    exam_id: int
    questions: list[QuizQuestionResponse]
    total: int
    duration_seconds: int
    started_at: str


class QuizExamAnswerItem(BaseModel):
    question_id: int = Field(..., ge=1)
    user_answer: str = Field(..., min_length=1, max_length=256)


class QuizExamSubmitRequest(BaseModel):
    exam_id: int = Field(..., ge=1)
    answers: list[QuizExamAnswerItem] = Field(..., min_length=1)
    elapsed_seconds: int = Field(..., ge=0)


class QuizExamSubmitResponse(BaseModel):
    exam_id: int
    total: int
    correct_count: int
    wrong_count: int
    unanswered_count: int
    score: float
    accuracy: float
    elapsed_seconds: int
    details: list[QuizSubmitResponse] = Field(default_factory=list)


class QuizExamListItem(BaseModel):
    id: int
    total: int
    correct_count: int | None = None
    score: float | None = None
    elapsed_seconds: int | None = None
    duration_seconds: int
    started_at: str
    status: str

    model_config = {"from_attributes": True}


class QuizExamDetailResponse(BaseModel):
    id: int
    total: int
    correct_count: int | None = None
    wrong_count: int | None = None
    score: float | None = None
    accuracy: float | None = None
    elapsed_seconds: int | None = None
    duration_seconds: int
    started_at: str
    submitted_at: str | None = None
    status: str
    details: list[QuizSubmitResponse] = Field(default_factory=list)


# ── 分类进度 ──────────────────────────────────────────────────

class QuizProgressItem(BaseModel):
    category_id: int
    category_name: str
    total: int
    answered: int
    correct: int
    accuracy: float


# ── 近期记录 ──────────────────────────────────────────────────

class QuizRecentItem(BaseModel):
    id: int
    question_id: int
    question_text: str
    question_type: str
    user_answer: str | None = None
    is_correct: bool | None = None
    updated_at: str | None = None
