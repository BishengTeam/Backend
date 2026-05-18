from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000, description="User message text")
    session_id: str | None = Field(None, description="Conversation ID; created by server on first use")


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    backend_type: str = Field(..., description="Chat backend type")


class QuickQuestionResponse(BaseModel):
    id: int
    question_text: str = Field(..., description="Recommended question text")
    category: str | None = Field(None, description="Question category")

    model_config = {"from_attributes": True}
