from fastapi import APIRouter

from app.api.auth import router as auth_router
from app.api.certification import router as cert_router
from app.api.chat import quick_router, router as chat_router
from app.api.system import router as system_router
from app.api.user import router as user_router

router = APIRouter(prefix="/api")
router.include_router(auth_router)
router.include_router(cert_router)
router.include_router(user_router)
router.include_router(chat_router)
router.include_router(system_router)
router.include_router(quick_router)
