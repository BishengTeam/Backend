from fastapi import APIRouter, Depends

from app.middleware.auth import get_current_user, get_current_user_optional
from app.models.user import User
from app.schemas.common import APIResponse, success
from app.schemas.zone import (
    ActivityZoneResponse,
    CertZoneResponse,
    CompetitionZoneResponse,
    EmploymentZoneResponse,
    HomeAggregationResponse,
    StudyZoneResponse,
)
from app.services.zone import ZoneService

router = APIRouter(prefix="/zones", tags=["专区"])


@router.get("", response_model=APIResponse[HomeAggregationResponse])
async def home_aggregation(
    current_user: User | None = Depends(get_current_user_optional),
) -> APIResponse[HomeAggregationResponse]:
    """首页聚合：Banner + 所有专区类型"""
    result = await ZoneService().get_home_aggregation()
    return success(data=result)


@router.get("/cert", response_model=APIResponse[CertZoneResponse])
async def cert_zone(
    current_user: User | None = Depends(get_current_user_optional),
) -> APIResponse[CertZoneResponse]:
    """认证专区：认证 zone + certification 列表"""
    result = await ZoneService().get_cert_zone()
    return success(data=result)


@router.get("/study", response_model=APIResponse[StudyZoneResponse])
async def study_zone(
    current_user: User | None = Depends(get_current_user_optional),
) -> APIResponse[StudyZoneResponse]:
    """学习专区：学习 zone + 课程列表"""
    result = await ZoneService().get_study_zone()
    return success(data=result)


@router.get("/competition", response_model=APIResponse[CompetitionZoneResponse])
async def competition_zone(
    current_user: User = Depends(get_current_user),
) -> APIResponse[CompetitionZoneResponse]:
    """竞赛专区：竞赛 zone + 报名数据（需登录）"""
    result = await ZoneService().get_competition_zone(user_id=current_user.id)
    return success(data=result)


@router.get("/activity", response_model=APIResponse[ActivityZoneResponse])
async def activity_zone(
    current_user: User = Depends(get_current_user),
) -> APIResponse[ActivityZoneResponse]:
    """活动专区：活动 zone + 活动列表（需登录）"""
    result = await ZoneService().get_activity_zone()
    return success(data=result)


@router.get("/employment", response_model=APIResponse[EmploymentZoneResponse])
async def employment_zone(
    current_user: User = Depends(get_current_user),
) -> APIResponse[EmploymentZoneResponse]:
    """就业专区：就业 zone + 岗位列表（需登录）"""
    result = await ZoneService().get_employment_zone()
    return success(data=result)
