from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapter.database import get_db
from app.port.config import settings
from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.schemas.common import APIResponse, success
from app.schemas.system import PosterResponse, ServerTimeResponse
from app.services.clock import db_now_utc

router = APIRouter(prefix="/system", tags=["系统"])


@router.get(
    "/time",
    response_model=APIResponse[ServerTimeResponse],
    summary="获取服务器当前时间",
    description="小程序可通过该接口检测本地时间偏移。返回数据库当前时间，并包含毫秒时间戳供倒计时等亚秒级场景校准使用。认证：无需登录。",
)
async def server_time(db: AsyncSession = Depends(get_db)) -> APIResponse[ServerTimeResponse]:
    now = await db_now_utc(db)
    return success(data=ServerTimeResponse(
        unix=int(now.timestamp()),
        unix_ms=int(now.timestamp() * 1000),
        iso=now,
    ))


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

