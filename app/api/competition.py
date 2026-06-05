from fastapi import APIRouter, Depends

from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.schemas.common import APIResponse, success
from app.schemas.competition import CompetitionRegResponse, CompetitionSignupRequest, CompetitionStatsItem, TrackListResponse
from app.services.competition import CompetitionService

router = APIRouter(prefix="/competition", tags=["竞赛"])


@router.get("/stats", response_model=APIResponse[list[CompetitionStatsItem]])
async def get_competition_stats():
    """按学校统计竞赛报名人数，按 count 降序排列"""
    stats = await CompetitionService().get_school_stats()
    return success(data=stats)


@router.get("/tracks", response_model=APIResponse[TrackListResponse])
async def get_competition_tracks():
    """获取所有不重复的赛道列表"""
    tracks = await CompetitionService().get_tracks()
    return success(data=TrackListResponse(tracks=tracks))


@router.post("/signup", response_model=APIResponse[CompetitionRegResponse])
async def signup_competition(
    body: CompetitionSignupRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[CompetitionRegResponse]:
    """竞赛报名"""
    result = await CompetitionService().signup(current_user.id, body)
    return success(data=CompetitionRegResponse.model_validate(result))