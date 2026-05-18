from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


QuizQuestionType = Literal["single_choice", "multiple_choice", "judge"]


class QuizCategoryResponse(BaseModel):
    id: int
    name: str = Field(..., description="分类名称")
    parent_id: int | None = Field(None, description="父级分类 ID，顶层分类为空")
    description: str | None = Field(None, description="分类描述")

    model_config = {"from_attributes": True}


class QuizCategoryTreeResponse(QuizCategoryResponse):
    children: list[QuizCategoryTreeResponse] = Field(default_factory=list, description="子分类列表")


class QuizQuestionQuery(BaseModel):
    category_id: int | None = Field(None, ge=1, description="分类 ID")
    question_type: QuizQuestionType | None = Field(None, description="题型：single_choice / multiple_choice / judge")
    page: int = Field(1, ge=1, description="页码，从 1 开始")
    page_size: int = Field(20, ge=1, le=100, description="每页数量，最大 100")


class QuizQuestionResponse(BaseModel):
    id: int
    category_id: int = Field(..., description="所属分类 ID")
    question_type: QuizQuestionType = Field(..., description="题型：single_choice / multiple_choice / judge")
    question_text: str = Field(..., description="题干")
    options: dict[str, Any] | None = Field(None, description="题目选项 JSON，判断题可为空")
    explanation: str | None = Field(None, description="题目解析")

    model_config = {"from_attributes": True}


class QuizSubmitRequest(BaseModel):
    question_id: int = Field(..., ge=1, description="题目 ID")
    user_answer: str = Field(..., min_length=1, max_length=256, description="用户答案")


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
    user_answer: str | None = Field(None, description="用户最近一次答案")
    is_correct: bool | None = Field(None, description="最近一次答题是否正确")
    is_wrong: bool = Field(..., description="是否在错题本")
    is_collected: bool = Field(..., description="是否已收藏")
    question: QuizQuestionResponse = Field(..., description="题目信息，不包含标准答案")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="更新时间")

    model_config = {"from_attributes": True}


class QuizToggleRequest(BaseModel):
    question_id: int = Field(..., ge=1, description="题目 ID")


class QuizToggleResponse(BaseModel):
    id: int = Field(..., description="答题记录 ID")
    question_id: int = Field(..., description="题目 ID")
    is_wrong: bool = Field(..., description="是否在错题本")
    is_collected: bool = Field(..., description="是否已收藏")
    updated_at: datetime = Field(..., description="更新时间")

    model_config = {"from_attributes": True}


class QuizWrongBookRequest(QuizToggleRequest):
    pass


class QuizWrongBookResponse(QuizToggleResponse):
    pass


class QuizCollectionRequest(QuizToggleRequest):
    pass


class QuizCollectionResponse(QuizToggleResponse):
    pass


class QuizCheckinRequest(BaseModel):
    questions_completed: int = Field(0, ge=0, description="当天完成题数")


class QuizCheckinResponse(BaseModel):
    id: int | None = Field(None, description="打卡记录 ID，未打卡时为空")
    checkin_date: date = Field(..., description="打卡日期")
    checked_in: bool = Field(..., description="当天是否已打卡")
    questions_completed: int = Field(..., ge=0, description="当天完成题数")
    consecutive_days: int = Field(..., ge=0, description="连续打卡天数")

    model_config = {"from_attributes": True}
