from fastapi import APIRouter, Depends, Path

from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.schemas.common import APIResponse, success
from app.schemas.share import ShareCreateRequest, ShareCreateResponse, SharePosterResponse, ShareResponse
from app.services.share import ShareService

router = APIRouter(prefix="/share", tags=["系统"])


@router.post("",
    response_model=APIResponse[ShareCreateResponse],
    summary="生成分享链接",
    description="""
小程序 **分享** 功能使用。

**使用场景**: 用户生成内容分享链接，用于分享课程、活动等

**请求体**: 分享目标类型和 ID

**响应**: 含分享码和分享链接

**认证**: 需登录
    """,
)
async def create_share(
    body: ShareCreateRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[ShareCreateResponse]:
    """生成分享链接"""
    result = await ShareService().create_share(current_user.id, body)
    return success(data=result)


@router.get("/poster",
    response_model=APIResponse[SharePosterResponse],
    summary="获取分享海报",
    description="""
小程序 **分享好友** 页使用。

**使用场景**: 分享页加载时获取后台配置的推广海报，全屏预览/长按保存到相册；
未配置时 `image_url` 为 null（前端入口置灰）。

**响应**: 海报图片 URL（可为 null）

**认证**: 需登录

> 注意：本路由必须注册在 `/{code}` 之前，否则 "poster" 会被当作分享码解析。
    """,
)
async def get_share_poster(
    current_user: User = Depends(get_current_user),
) -> APIResponse[SharePosterResponse]:
    """获取当前启用的分享海报"""
    return success(data=await ShareService().get_active_poster())


@router.get("/{code}",
    response_model=APIResponse[ShareResponse],
    summary="分享追踪",
    description="""
小程序 **分享** 功能使用。

**使用场景**: 被分享者通过分享码访问，记录访问并返回目标内容信息

**路径参数**:
- `code`: 分享码

**响应**: 分享目标信息

**认证**: 无需登录
    """,
)
async def track_share(
    code: str = Path(..., description="分享码"),
) -> APIResponse[ShareResponse]:
    """分享追踪：记录访问并返回目标信息"""
    result = await ShareService().track_visit(code)
    return success(data=result)