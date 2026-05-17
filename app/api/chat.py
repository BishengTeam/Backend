from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.middleware.auth import get_current_user
from app.models.user import User
from app.schemas.chat import ChatRequest, ChatResponse, QuickQuestionResponse
from app.schemas.common import APIResponse, success
from app.services.chat import ChatService

router = APIRouter(prefix="/chat", tags=["客服"])

quick_router = APIRouter()


@quick_router.get("/quick-questions")
async def quick_questions(
    current_user: User = Depends(get_current_user),
) -> APIResponse[list[QuickQuestionResponse]]:
    """获取推荐问题列表"""
    result = await ChatService().get_quick_questions()
    return success(data=result)


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
    message: str = Query(..., min_length=1, max_length=2000, description="用户消息文本"),
    session_id: str | None = Query(None, description="会话 ID，首次为空则由服务端创建"),
    current_user: User = Depends(get_current_user),
):
    """SSE 流式消息响应"""
    return StreamingResponse(
        ChatService().stream_message(current_user.id, message, session_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
