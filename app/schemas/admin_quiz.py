from pydantic import BaseModel, Field


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
    question_type: str = Field(..., min_length=1, max_length=16)
    question_text: str = Field(..., min_length=1, max_length=1024)
    options: dict | None = None
    correct_answer: str = Field(..., min_length=1, max_length=256)
    explanation: str | None = Field(None, max_length=1024)


class AdminQuizQuestionUpdate(BaseModel):
    category_id: int | None = Field(None, ge=1)
    question_type: str | None = Field(None, min_length=1, max_length=16)
    question_text: str | None = Field(None, min_length=1, max_length=1024)
    options: dict | None = None
    correct_answer: str | None = Field(None, min_length=1, max_length=256)
    explanation: str | None = Field(None, max_length=1024)


class AdminQuizQuestionItem(BaseModel):
    question_text: str = Field(..., min_length=1, max_length=1024)
    options: dict
    correct_answer: str = Field(..., min_length=1, max_length=256)
    question_type: str = Field(default="single", min_length=1, max_length=16)
    explanation: str | None = Field(None, max_length=1024)


class AdminQuizImportJsonRequest(BaseModel):
    category_id: int = Field(..., ge=1)
    questions: list[AdminQuizQuestionItem] = Field(..., min_length=1)
