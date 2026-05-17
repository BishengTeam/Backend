from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000, description="用户消息文本")
    session_id: str | None = Field(None, description="会话 ID，首次为空则由服务端创建")


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    backend_type: Literal["manual"] = Field(..., description="客服后端类型")


class QuickQuestionResponse(BaseModel):
    id: int
    question_text: str = Field(..., description="推荐问题文本")
    category: str | None = Field(None, description="问题分类")

    model_config = {"from_attributes": True}
