from fastapi import APIRouter, Depends

from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.schemas.common import APIResponse, success
from app.schemas.competition import (
    CompetitionListItem,
    CompetitionRegResponse,
    CompetitionSignupRequest,
)
from app.services.competition import CompetitionService

router = APIRouter(prefix="/competition", tags=["竞赛"])


@router.get("/list",
    response_model=APIResponse[list[CompetitionListItem]],
    summary="赛事列表",
    description="""
小程序 **竞赛专区** 页面使用。

**使用场景**: 获取已发布的赛事（含赛道与报名余量）

**响应**: 赛事列表

**认证**: 无需登录
    """,
)
async def list_competitions() -> APIResponse[list[CompetitionListItem]]:
    result = await CompetitionService().list_events(active_only=True)
    return success(data=result)


@router.post("/signup",
    response_model=APIResponse[CompetitionRegResponse],
    summary="竞赛报名",
    description="""
小程序 **赛事详情** 页面使用。

**使用场景**: 用户选择赛道后提交竞赛报名（学校/姓名/手机）

**请求体**: track_id + school + real_name + phone

**校验**: 赛事发布中 / 报名截止前 / 赛道未满 / 未重复报名

**认证**: 需登录
    """,
)
async def signup_competition(
    body: CompetitionSignupRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[CompetitionRegResponse]:
    """竞赛报名"""
    result = await CompetitionService().signup(current_user.id, body)
    return success(data=CompetitionRegResponse.model_validate(result))
