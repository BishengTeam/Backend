from fastapi import APIRouter, Depends, Query

from app.middleware.auth import get_current_user
from app.models.user import User
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.points import (
    PointsBalanceResponse,
    PointsClaimRequest,
    PointsClaimResponse,
    PointsHistoryResponse,
    PointsRedeemRequest,
    PointsRedeemResponse,
)
from app.services.points import PointsService

router = APIRouter(prefix="/points", tags=["积分"])


@router.get("", response_model=APIResponse[PointsBalanceResponse])
async def get_points(
    current_user: User = Depends(get_current_user),
) -> APIResponse[PointsBalanceResponse]:
    result = await PointsService().get_balance(current_user.id)
    return success(data=result)


@router.get("/history", response_model=APIResponse[PaginatedData[PointsHistoryResponse]])
async def list_points_history(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[PaginatedData[PointsHistoryResponse]]:
    result = await PointsService().list_history(
        current_user.id,
        page=page,
        page_size=page_size,
    )
    return success(data=result)


@router.post("/claim", response_model=APIResponse[PointsClaimResponse])
async def claim_points(
    body: PointsClaimRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[PointsClaimResponse]:
    result = await PointsService().claim_points(current_user.id, body)
    return success(data=result)


@router.post("/redeem", response_model=APIResponse[PointsRedeemResponse])
async def redeem_points(
    body: PointsRedeemRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[PointsRedeemResponse]:
    result = await PointsService().redeem_points(current_user.id, body)
    return success(data=result)
