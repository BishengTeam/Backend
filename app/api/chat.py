from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.middleware.auth import get_current_user
from app.models.user import User
from app.schemas.chat import ChatRequest, ChatResponse
from app.schemas.common import APIResponse, success
from app.services.chat import ChatService

router = APIRouter(prefix="/chat", tags=["客服"])


@router.post("")
async def chat(
    body: ChatRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[ChatResponse]:
    """发送消息，返回客服回复"""
    result = await ChatService().process_message(current_user.id, body.message, body.session_id)
    return success(data=result)


@router.get("/stream")
async def chat_stream(
    message: str = Query(..., min_length=1, max_length=2000),
    session_id: str | None = Query(None),
    current_user: User = Depends(get_current_user),
):
    """SSE 流式消息响应"""
    return StreamingResponse(
        ChatService().stream_message(current_user.id, message, session_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
