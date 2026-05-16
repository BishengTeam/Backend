from fastapi import APIRouter

from app.core.config import settings
from app.schemas.common import success

router = APIRouter(prefix="/system", tags=["系统"])


@router.get("/poster")
async def get_login_poster():
    return success(data={"url": settings.LOGIN_POSTER_URL})
