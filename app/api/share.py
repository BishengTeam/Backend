from fastapi import APIRouter, Depends, Path

from app.middleware.auth import get_current_user
from app.models.user import User
from app.schemas.common import APIResponse, success
from app.schemas.share import ShareCreateRequest, ShareCreateResponse, ShareResponse
from app.services.share import ShareService

router = APIRouter(prefix="/share", tags=["分享"])


@router.post("", response_model=APIResponse[ShareCreateResponse])
async def create_share(
    body: ShareCreateRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[ShareCreateResponse]:
    """生成分享链接"""
    result = await ShareService().create_share(current_user.id, body)
    return success(data=result)


@router.get("/{code}", response_model=APIResponse[ShareResponse])
async def track_share(
    code: str = Path(..., description="分享码"),
) -> APIResponse[ShareResponse]:
    """分享追踪：记录访问并返回目标信息"""
    result = await ShareService().track_visit(code)
    return success(data=result)
