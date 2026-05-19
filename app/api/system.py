from fastapi import APIRouter, Depends

from app.core.config import settings
from app.middleware.auth import get_current_user
from app.models.user import User
from app.schemas.common import APIResponse, success
from app.schemas.system import PosterResponse

router = APIRouter(prefix="/system", tags=["系统"])


@router.get("/poster", response_model=APIResponse[PosterResponse])
async def get_login_poster(
    current_user: User = Depends(get_current_user),
) -> APIResponse[PosterResponse]:
    return success(data=PosterResponse(url=settings.LOGIN_POSTER_URL))
