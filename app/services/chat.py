import json
import uuid
from typing import AsyncGenerator

from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import get_db_ctx
from app.core.exceptions import BusinessException
from app.core.redis import redis_client
from app.integrations.chat_backend import ChatBackend, DifyChatBackend
from app.models.conversation import Conversation
from app.models.quick_question import QuickQuestion
from app.schemas.chat import ChatResponse, QuickQuestionResponse

SESSION_TTL = 3600
CONTEXT_MAX_MESSAGES = 20


class ChatService:
    def __init__(self, backend: ChatBackend | None = None) -> None:
        self._backend = backend

    @property
    def backend(self) -> ChatBackend:
        if self._backend is None:
            self._backend = self._build_backend()
        return self._backend

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
        await self._save_conversation(user_id, session_id, context, self.backend.type)
        return ChatResponse(session_id=session_id, reply=reply, backend_type=self.backend.type)

    async def stream_message(
        self, user_id: int, message: str, session_id: str | None
    ) -> AsyncGenerator[str, None]:
        if session_id is None:
            session_id = str(uuid.uuid4())
        context = await self._get_context(session_id)
        context.append({"role": "user", "content": message})
        backend_type = "unavailable"
        full_reply = ""
        try:
            backend_type = self.backend.type
            async for chunk in self.backend.stream_message(user_id, message, context):
                full_reply += chunk
                yield f"data: {json.dumps({'chunk': chunk, 'session_id': session_id}, ensure_ascii=False)}\n\n"
            context.append({"role": "assistant", "content": full_reply})
            if len(context) > CONTEXT_MAX_MESSAGES:
                context = context[-CONTEXT_MAX_MESSAGES:]
            yield f"data: {json.dumps({'done': True, 'session_id': session_id, 'reply': full_reply}, ensure_ascii=False)}\n\n"
        except BusinessException as exc:
            yield f"data: {json.dumps({'error': exc.message, 'session_id': session_id}, ensure_ascii=False)}\n\n"
        except Exception:
            yield f"data: {json.dumps({'error': 'Service unavailable, please retry', 'session_id': session_id}, ensure_ascii=False)}\n\n"
        finally:
            await self._save_context(session_id, context)
            await self._save_conversation(user_id, session_id, context, backend_type)

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

    def _build_backend(self) -> ChatBackend:
        backend_type = settings.CHAT_BACKEND.strip().lower()
        if backend_type in {"", "disabled"}:
            raise BusinessException("Chat backend is not configured; set CHAT_BACKEND")
        if backend_type == "dify":
            if not settings.DIFY_API_BASE or not settings.DIFY_API_KEY:
                raise BusinessException("Dify chat backend requires DIFY_API_BASE and DIFY_API_KEY")
            return DifyChatBackend(api_base=settings.DIFY_API_BASE, api_key=settings.DIFY_API_KEY)
        raise BusinessException(f"Unsupported chat backend: {settings.CHAT_BACKEND}")

    async def _save_conversation(
        self,
        user_id: int,
        session_id: str,
        context: list[dict],
        backend_type: str,
    ) -> None:
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
                    backend_type=backend_type,
                )
                db.add(conv)
            else:
                conv.messages = {"messages": context}
                conv.backend_type = backend_type
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
