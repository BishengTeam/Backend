from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class AdminQuizQuestionResponse(BaseModel):
    """Admin-facing question response that includes the correct answer."""

    id: int
    category_id: int
    question_type: Literal["single_choice", "multiple_choice", "judge"]
    question_text: str
    options: dict | None = None
    correct_answer: str
    explanation: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AdminQuizCategoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    parent_id: int | None = Field(None, ge=1)
    description: str | None = Field(None, max_length=256)


class AdminQuizCategoryUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    parent_id: int | None = Field(None, ge=1)
    description: str | None = Field(None, max_length=256)


class AdminQuizQuestionCreate(BaseModel):
    category_id: int = Field(..., ge=1)
    question_type: Literal["single_choice", "multiple_choice", "judge"] = Field(..., min_length=1, max_length=16)
    question_text: str = Field(..., min_length=1, max_length=1024)
    options: dict | None = None
    correct_answer: str = Field(..., min_length=1, max_length=256)
    explanation: str | None = Field(None, max_length=1024)
    image_urls: list[str] = Field(default_factory=list, max_length=9)


class AdminQuizQuestionUpdate(BaseModel):
    category_id: int | None = Field(None, ge=1)
    question_type: Literal["single_choice", "multiple_choice", "judge"] | None = Field(None, min_length=1, max_length=16)
    question_text: str | None = Field(None, min_length=1, max_length=1024)
    options: dict | None = None
    correct_answer: str | None = Field(None, min_length=1, max_length=256)
    explanation: str | None = Field(None, max_length=1024)
    image_urls: list[str] | None = Field(None, max_length=9)


class AdminQuizQuestionItem(BaseModel):
    question_text: str = Field(..., min_length=1, max_length=1024)
    options: dict
    correct_answer: str = Field(..., min_length=1, max_length=256)
    question_type: Literal["single_choice", "multiple_choice", "judge"] = Field(default="single_choice", min_length=1, max_length=16)
    explanation: str | None = Field(None, max_length=1024)
    image_urls: list[str] = Field(default_factory=list, max_length=9)


class AdminQuizImportJsonRequest(BaseModel):
    category_id: int = Field(..., ge=1)
    questions: list[AdminQuizQuestionItem] = Field(..., min_length=1)
