from hmac import compare_digest

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import select

from app.adapter.database import get_db
from app.domain.user.src.index import User
from app.middleware.auth import get_current_user, require_permission
from app.port.config import settings
from app.port.exceptions import NotFoundException, UnauthorizedException
from app.schemas.common import APIResponse, success
from app.schemas.videoweb import (
    VideoWebCodeCreate,
    VideoWebCodeStatus,
    VideoWebUserLookupResponse,
)
from app.services.videoweb import VideoWebService


user_router = APIRouter(prefix="/videoweb", tags=["课程视频站"])
admin_router = APIRouter(prefix="/videoweb", tags=["管理后台-课程视频站"])
internal_router = APIRouter(prefix="/internal/videoweb", tags=["课程视频站内部接口"])
router = user_router


@internal_router.get(
    "/users/lookup",
    response_model=APIResponse[VideoWebUserLookupResponse],
    include_in_schema=False,
)
async def lookup_video_web_user(
    phone: str = Query(..., min_length=11, max_length=11),
    x_service_token: str | None = Header(default=None),
    db=Depends(get_db),
) -> APIResponse[VideoWebUserLookupResponse]:
    expected = settings.VIDEOWEB_LOOKUP_SERVICE_TOKEN
    if not expected or not x_service_token or not compare_digest(x_service_token, expected):
        raise UnauthorizedException("服务认证失败")
    user = await db.scalar(
        select(User).where(User.phone == phone, User.is_active.is_(True))
        .order_by(User.id.asc())
        .limit(1)
    )
    if user is None:
        raise NotFoundException("小程序用户")
    return success(
        data=VideoWebUserLookupResponse(
            user_id=user.id,
            phone=user.phone or phone,
            is_active=user.is_active,
        )
    )


@user_router.get("/codes", response_model=APIResponse[list[dict]])
async def my_video_web_codes(
    current_user: User = Depends(get_current_user),
) -> APIResponse[list[dict]]:
    return success(data=await VideoWebService.list_user_codes(current_user.id))


@admin_router.get("/codes", response_model=APIResponse[dict])
async def list_video_web_codes(
    course_id: int | None = Query(default=None, gt=0),
    status: str | None = Query(default=None, max_length=16),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _admin=Depends(require_permission("course:read")),
) -> APIResponse[dict]:
    data = await VideoWebService.list_codes(
        course_id=course_id,
        status=status,
        page=page,
        page_size=page_size,
    )
    if isinstance(data, dict):
        data.setdefault("page", page)
        data.setdefault("page_size", page_size)
    return success(data=data)


@admin_router.post("/codes", response_model=APIResponse[list[dict]])
async def create_video_web_codes(
    body: VideoWebCodeCreate,
    _admin=Depends(require_permission("course:write")),
) -> APIResponse[list[dict]]:
    result = await VideoWebService.issue_code(
        course_id=body.course_id,
        order_id=None,
        miniapp_user_id=None,
        created_by="admin",
        note=body.note,
    )
    # VideoWeb's order API returns one code. For a manual batch, call it once
    # per requested code and let Admin show the returned codes immediately.
    codes = list(result)
    for _ in range(body.quantity - 1):
        codes.extend(
            await VideoWebService.issue_code(
                course_id=body.course_id,
                order_id=None,
                miniapp_user_id=None,
                created_by="admin",
                note=body.note,
            )
        )
    return success(data=codes, message="兑换码生成成功")


@admin_router.post("/codes/{code_id}/revoke", response_model=APIResponse[dict])
async def revoke_video_web_code(
    code_id: int,
    body: VideoWebCodeStatus | None = None,
    _admin=Depends(require_permission("course:write")),
) -> APIResponse[dict]:
    return success(
        data=await VideoWebService.change_code_status(
            code_id, "revoke", body.reason if body else None
        ),
        message="兑换码已撤销",
    )


@admin_router.post("/codes/{code_id}/refund", response_model=APIResponse[dict])
async def refund_video_web_code(
    code_id: int,
    body: VideoWebCodeStatus | None = None,
    _admin=Depends(require_permission("course:write")),
) -> APIResponse[dict]:
    return success(
        data=await VideoWebService.change_code_status(
            code_id, "refund", body.reason if body else None
        ),
        message="兑换码已退款回收",
    )
