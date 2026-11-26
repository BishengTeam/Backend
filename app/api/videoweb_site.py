"""B7 视频站站点接口（learn.ztyyx.cn 前端直接调用）。

与小程序 C 端接口分离：返回字段按视频站前端 types.ts 契约。
"""

from fastapi import APIRouter, Body, Depends, Path

from app.domain.user.src.index import User
from app.middleware.auth import get_current_user
from app.schemas.common import APIResponse, success
from app.schemas.videoweb import VideoWebLoginExchangeRequest
from app.services.videoweb_site import VideoWebSiteService

router = APIRouter(prefix="/videoweb/site", tags=["课程视频站-站点接口"])


@router.post("/login", response_model=APIResponse[dict], summary="登录码换会话")
async def site_login(body: VideoWebLoginExchangeRequest) -> APIResponse[dict]:
    return success(data=await VideoWebSiteService.login(body.login_code))


@router.get("/me", response_model=APIResponse[dict])
async def site_me(current_user: User = Depends(get_current_user)) -> APIResponse[dict]:
    return success(data=await VideoWebSiteService.me(current_user))


@router.get("/courses", response_model=APIResponse[list[dict]])
async def site_courses(
    current_user: User = Depends(get_current_user),
) -> APIResponse[list[dict]]:
    return success(data=await VideoWebSiteService.my_courses(current_user.id))


@router.get("/courses/{course_id}", response_model=APIResponse[dict])
async def site_course(
    course_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse[dict]:
    return success(
        data=await VideoWebSiteService.course_detail(current_user.id, course_id)
    )


@router.get("/courses/{course_id}/chapters", response_model=APIResponse[dict])
async def site_chapters(
    course_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse[dict]:
    return success(
        data=await VideoWebSiteService.chapters(current_user.id, course_id)
    )


@router.post("/courses/{course_id}/chapters/{chapter_id}/playback")
async def site_playback(
    course_id: int = Path(..., ge=1),
    chapter_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse[dict]:
    return success(
        data=await VideoWebSiteService.playback(
            current_user.id, course_id, chapter_id, document=False
        )
    )


@router.post("/courses/{course_id}/chapters/{chapter_id}/resource")
async def site_resource(
    course_id: int = Path(..., ge=1),
    chapter_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse[dict]:
    return success(
        data=await VideoWebSiteService.playback(
            current_user.id, course_id, chapter_id, document=True
        )
    )


@router.post("/courses/{course_id}/chapters/{chapter_id}/progress")
async def site_progress(
    course_id: int = Path(..., ge=1),
    chapter_id: int = Path(..., ge=1),
    payload: dict = Body(...),
    current_user: User = Depends(get_current_user),
) -> APIResponse[dict]:
    return success(
        data=await VideoWebSiteService.save_progress(
            current_user.id, course_id, chapter_id, payload
        )
    )
