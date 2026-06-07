from fastapi import APIRouter, Depends

from app.port.config import settings
from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.schemas.common import APIResponse, success
from app.schemas.system import PosterResponse

router = APIRouter(prefix="/system", tags=["系统"])


@router.get("/poster",
    response_model=APIResponse[PosterResponse],
    summary="获取登录页海报",
    description="""
小程序 **登录页** 使用。

**使用场景**: 登录页加载时获取海报图片，用于登录页背景展示

**响应**: 海报图片 URL

**认证**: 需登录
    """,
)
async def get_login_poster(
    current_user: User = Depends(get_current_user),
) -> APIResponse[PosterResponse]:
    return success(data=PosterResponse(url=settings.LOGIN_POSTER_URL))


