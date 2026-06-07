from fastapi import APIRouter, Depends

from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.schemas.common import APIResponse, success
from app.schemas.competition import CompetitionRegResponse, CompetitionSignupRequest, CompetitionStatsItem, TrackListResponse
from app.services.competition import CompetitionService

router = APIRouter(prefix="/competition", tags=["竞赛"])


@router.get("/stats",
    response_model=APIResponse[list[CompetitionStatsItem]],
    summary="竞赛统计",
    description="""
小程序 **竞赛** 页面使用。

**使用场景**: 按学校维度统计竞赛报名人数，展示排行榜
**响应**: 学校统计数据列表，按报名人数降序排列
**认证**: 无需登录
    """,
)
async def get_competition_stats():
    stats = await CompetitionService().get_school_stats()
    return success(data=stats)


@router.get("/tracks",
    response_model=APIResponse[TrackListResponse],
    summary="竞赛赛道列表",
    description="""
小程序 **竞赛** 页面使用。

**使用场景**: 获取所有不重复的竞赛赛道列表，供报名时选择

**响应**: 赛道名称列表

**认证**: 无需登录
    """,
)
async def get_competition_tracks():
    """获取所有不重复的赛道列表"""
    tracks = await CompetitionService().get_tracks()
    return success(data=TrackListResponse(tracks=tracks))


@router.post("/signup",
    response_model=APIResponse[CompetitionRegResponse],
    summary="竞赛报名",
    description="""
小程序 **竞赛** 页面使用。

**使用场景**: 用户选择赛道后提交竞赛报名信息

**请求体**: 含赛道、个人信息等报名参数

**响应**: 报名结果，含报名编号

**认证**: 需登录（建议已实名认证）
    """,
)
async def signup_competition(
    body: CompetitionSignupRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[CompetitionRegResponse]:
    """竞赛报名"""
    result = await CompetitionService().signup(current_user.id, body)
    return success(data=CompetitionRegResponse.model_validate(result))