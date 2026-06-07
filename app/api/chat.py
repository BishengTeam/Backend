from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.schemas.chat import ChatRequest, ChatResponse, QuickQuestionResponse
from app.schemas.common import APIResponse, success
from app.services.chat import ChatService

router = APIRouter(prefix="/chat", tags=["客服工单"])

quick_router = APIRouter()


@quick_router.get("/quick-questions",
    response_model=APIResponse[list[QuickQuestionResponse]],
    summary="推荐问题",
    description="""
小程序 **客服** 页面使用。

**使用场景**: 进入客服对话页时获取推荐问题列表，引导用户快速发起咨询

**响应**: 推荐问题列表

**认证**: 需登录
    """,
)
async def quick_questions(
    current_user: User = Depends(get_current_user),
) -> APIResponse[list[QuickQuestionResponse]]:
    """获取推荐问题列表"""
    result = await ChatService().get_quick_questions()
    return success(data=result)


@router.post("",
    response_model=APIResponse[ChatResponse],
    summary="发送客服消息",
    description="""
小程序 **客服** 页面使用。

**使用场景**: 用户发送消息后，返回 AI 客服的回复内容

**请求体**:
- `message`: 用户消息文本
- `session_id`: 会话 ID，首次为空则由服务端创建

**响应**: AI 回复消息，含 session_id

**认证**: 需登录
    """,
)
async def chat(
    body: ChatRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[ChatResponse]:
    """发送消息，返回客服回复"""
    result = await ChatService().process_message(current_user.id, body.message, body.session_id)
    return success(data=result)


@router.get("/stream",
    summary="SSE流式消息",
    description="""
小程序 **客服** 页面使用。

**使用场景**: 通过 Server-Sent Events 流式接收 AI 客服的逐字回复，实现打字机效果

**查询参数**:
- `message`: 用户消息文本（1-2000 字符）
- `session_id`: 会话 ID，首次为空则由服务端创建

**响应**: SSE 事件流，`text/event-stream`

**注意**: 前端需使用 EventSource 或 fetch + ReadableStream 消费流式响应

**认证**: 需登录
    """,
)
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