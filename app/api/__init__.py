from fastapi import APIRouter

from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.system import router as system_router
from app.api.user import router as user_router
from app.schemas.chat import QuickQuestionResponse
from app.services.chat import ChatService

router = APIRouter(prefix="/api")
router.include_router(auth_router)
router.include_router(user_router)
router.include_router(chat_router)
router.include_router(system_router)


@router.get("/quick-questions", response_model=list[QuickQuestionResponse])
async def quick_questions():
    """获取推荐问题列表"""
    return await ChatService().get_quick_questions()
