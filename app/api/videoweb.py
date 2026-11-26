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
    VideoWebAccessStatus,
    VideoWebCodeRedeemRequest,
    VideoWebCodeCreate,
    VideoWebCodeStatus,
    VideoWebLoginCodeResponse,
    VideoWebLoginExchangeRequest,
    VideoWebLoginExchangeResponse,
    VideoWebUserLookupResponse,
)
from app.services.videoweb import VideoWebService
from app.services.course_code import CourseCodeService
from app.services.course_code_admin import CourseCodeAdminService
from app.services.videoweb_admin import VideoWebAdminService


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
    # vweb001 收编：我的兑换码直读 Backend 本地表，不再转发视频站。
    return success(data=await CourseCodeService.list_for_user(current_user.id))


@user_router.post(
    "/codes/redeem",
    response_model=APIResponse[dict],
    summary="兑换课程码（仅限本人持有的码）",
)
async def redeem_video_web_code(
    body: VideoWebCodeRedeemRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[dict]:
    return success(
        data=await CourseCodeService.redeem(current_user.id, body.code),
        message="兑换成功",
    )


@user_router.post(
    "/login-code",
    response_model=APIResponse[VideoWebLoginCodeResponse],
    summary="生成课程视频站一次性登录码",
)
async def create_video_web_login_code(
    current_user: User = Depends(get_current_user),
) -> APIResponse[VideoWebLoginCodeResponse]:
    login_code = await VideoWebService.create_login_code(current_user.id)
    return success(
        data=VideoWebLoginCodeResponse(
            login_code=login_code,
            expires_in=settings.VIDEOWEB_LOGIN_CODE_TTL_SECONDS,
        ),
        message="登录码已生成",
    )


@internal_router.post(
    "/login/exchange",
    response_model=APIResponse[VideoWebLoginExchangeResponse],
    include_in_schema=False,
)
async def exchange_video_web_login_code(
    body: VideoWebLoginExchangeRequest,
    x_service_token: str | None = Header(default=None),
    db=Depends(get_db),
) -> APIResponse[VideoWebLoginExchangeResponse]:
    expected = settings.VIDEOWEB_LOOKUP_SERVICE_TOKEN
    if not expected or not x_service_token or not compare_digest(x_service_token, expected):
        raise UnauthorizedException("服务认证失败")
    user_id = await VideoWebService.exchange_login_code(body.login_code)
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundException("小程序用户")
    return success(
        data=VideoWebLoginExchangeResponse(
            user_id=user.id,
            phone=user.phone,
            is_active=user.is_active,
        )
    )


@admin_router.get("/users", response_model=APIResponse[dict])
async def search_video_web_users(
    keyword: str | None = Query(default=None, max_length=64),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
    _admin=Depends(require_permission("course:read")),
) -> APIResponse[dict]:
    """生成兑换码时选择归属用户（手机号前缀 / 姓名 / 昵称模糊）。"""
    return success(
        data=await CourseCodeAdminService.search_users(
            keyword=keyword, page=page, page_size=page_size
        )
    )


@admin_router.get("/learners", response_model=APIResponse[dict])
async def list_videoweb_learners(
    keyword: str | None = Query(default=None, max_length=64),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=2000),
    _admin=Depends(require_permission("course:read")),
) -> APIResponse[dict]:
    """学习授权页：学习者列表（授权数 / 最后学习 / 视频站访问状态）。"""
    return success(
        data=await VideoWebAdminService.list_learners(
            keyword=keyword, page=page, page_size=page_size
        )
    )


@admin_router.get(
    "/learners/{user_id}/entitlements", response_model=APIResponse[list[dict]]
)
async def list_videoweb_learner_entitlements(
    user_id: int,
    _admin=Depends(require_permission("course:read")),
) -> APIResponse[list[dict]]:
    return success(data=await VideoWebAdminService.list_entitlements(user_id))


@admin_router.post(
    "/learners/{user_id}/access-status", response_model=APIResponse[dict]
)
async def set_videoweb_learner_access(
    user_id: int,
    body: VideoWebAccessStatus,
    _admin=Depends(require_permission("course:write")),
) -> APIResponse[dict]:
    return success(
        data=await VideoWebAdminService.set_access(
            user_id, enabled=body.enabled, admin_id=_admin.id
        ),
        message="已启用视频站访问" if body.enabled else "已禁用视频站访问",
    )


@admin_router.get("/codes", response_model=APIResponse[dict])
async def list_video_web_codes(
    course_id: int | None = Query(default=None, gt=0),
    status: str | None = Query(default=None, max_length=16),
    owner_user_id: int | None = Query(default=None, gt=0),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=2000),
    _admin=Depends(require_permission("course:read")),
) -> APIResponse[dict]:
    return success(
        data=await CourseCodeAdminService.list_codes(
            course_id=course_id,
            status=status,
            owner_user_id=owner_user_id,
            page=page,
            page_size=page_size,
        )
    )


@admin_router.post("/codes", response_model=APIResponse[list[dict]])
async def create_video_web_codes(
    body: VideoWebCodeCreate,
    _admin=Depends(require_permission("course:write")),
) -> APIResponse[list[dict]]:
    codes = await CourseCodeAdminService.issue(
        course_id=body.course_id,
        quantity=body.quantity,
        note=body.note,
        admin_id=_admin.id,
    )
    return success(data=codes, message="兑换码生成成功")


@admin_router.post("/codes/{code_id}/revoke", response_model=APIResponse[dict])
async def revoke_video_web_code(
    code_id: int,
    body: VideoWebCodeStatus | None = None,
    _admin=Depends(require_permission("course:write")),
) -> APIResponse[dict]:
    return success(
        data=await CourseCodeAdminService.change_status(
            code_id, "revoke", body.reason if body else None, _admin.id
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
        data=await CourseCodeAdminService.change_status(
            code_id, "refund", body.reason if body else None, _admin.id
        ),
        message="兑换码已退款回收",
    )
