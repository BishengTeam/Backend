import json
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator

from sqlalchemy import func, select

from app.core.database import get_db_ctx
from app.core.redis import redis_client
from app.integrations.chat_backend import ManualChatBackend
from app.models.conversation import Conversation
from app.models.quick_question import QuickQuestion
from app.schemas.chat import ChatResponse, QuickQuestionResponse

SESSION_TTL = 3600
CONTEXT_MAX_MESSAGES = 20


class ChatService:
    def __init__(self):
        self.backend = ManualChatBackend()

    async def process_message(self, user_id: int, message: str, session_id: str | None) -> ChatResponse:
        if session_id is None:
            session_id = str(uuid.uuid4())
        context = await self._get_context(session_id)
        context.append({"role": "user", "content": message})
        reply = await self.backend.send_message(user_id, message, context)
        context.append({"role": "assistant", "content": reply})
        if len(context) > CONTEXT_MAX_MESSAGES:
            context = context[-CONTEXT_MAX_MESSAGES:]
        await self._save_context(session_id, context)
        await self._save_conversation(user_id, session_id, context)
        return ChatResponse(session_id=session_id, reply=reply, backend_type=self.backend.type)

    async def stream_message(
        self, user_id: int, message: str, session_id: str | None
    ) -> AsyncGenerator[str, None]:
        if session_id is None:
            session_id = str(uuid.uuid4())
        context = await self._get_context(session_id)
        context.append({"role": "user", "content": message})
        full_reply = ""
        try:
            async for chunk in self.backend.stream_message(user_id, message, context):
                full_reply += chunk
                yield f"data: {json.dumps({'chunk': chunk, 'session_id': session_id})}\n\n"
            context.append({"role": "assistant", "content": full_reply})
            if len(context) > CONTEXT_MAX_MESSAGES:
                context = context[-CONTEXT_MAX_MESSAGES:]
            yield f"data: {json.dumps({'done': True, 'session_id': session_id, 'reply': full_reply})}\n\n"
        except Exception:
            yield f"data: {json.dumps({'error': '服务异常，请重试', 'session_id': session_id})}\n\n"
        finally:
            await self._save_context(session_id, context)
            await self._save_conversation(user_id, session_id, context)

    QUICK_QUESTIONS_LIMIT = 6

    async def get_quick_questions(self) -> list[QuickQuestionResponse]:
        async with get_db_ctx() as db:
            result = await db.execute(
                select(QuickQuestion)
                .where(QuickQuestion.is_active == True)
                .order_by(func.random())
                .limit(self.QUICK_QUESTIONS_LIMIT)
            )
            questions = result.scalars().all()
            return [QuickQuestionResponse.model_validate(q) for q in questions]

    async def _save_conversation(self, user_id: int, session_id: str, context: list[dict]) -> None:
        async with get_db_ctx() as db:
            conv = (
                await db.execute(
                    select(Conversation).where(
                        Conversation.user_id == user_id,
                        Conversation.session_id == session_id,
                    )
                )
            ).scalar_one_or_none()
            if conv is None:
                conv = Conversation(
                    user_id=user_id,
                    session_id=session_id,
                    messages={"messages": context},
                    backend_type=self.backend.type,
                )
                db.add(conv)
            else:
                conv.messages = {"messages": context}
            await db.commit()

    async def _get_context(self, session_id: str) -> list[dict]:
        key = f"chat:session:{session_id}"
        data = await redis_client.get(key)
        if data:
            return json.loads(data)
        return []

    async def _save_context(self, session_id: str, context: list[dict]) -> None:
        key = f"chat:session:{session_id}"
        await redis_client.setex(key, SESSION_TTL, json.dumps(context, ensure_ascii=False))
