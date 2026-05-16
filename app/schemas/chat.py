from datetime import datetime

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    session_id: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    backend_type: str


class QuickQuestionResponse(BaseModel):
    id: int
    question_text: str
    category: str | None = None

    model_config = {"from_attributes": True}
