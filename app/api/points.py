from fastapi import APIRouter, Depends, Query

from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
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
_service = PointsService()


@router.get("",
    response_model=APIResponse[PointsBalanceResponse],
    summary="积分余额",
    description="""
小程序 **积分** 页面使用。

**使用场景**: 查看当前用户的积分余额

**响应**: 用户积分余额

**认证**: 需登录
    """,
)
async def get_points(
    current_user: User = Depends(get_current_user),
) -> APIResponse[PointsBalanceResponse]:
    result = await _service.get_balance(current_user.id)
    return success(data=result)


@router.get("/history",
    response_model=APIResponse[PaginatedData[PointsHistoryResponse]],
    summary="积分记录",
    description="""
小程序 **积分** 页面使用。

**使用场景**: 查看当前用户的积分变动记录，支持分页

**查询参数**:
- `page`: 页码，从 1 开始
- `page_size`: 每页数量，范围 1-100

**响应**: 分页积分历史记录

**认证**: 需登录
    """,
)
async def list_points_history(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[PaginatedData[PointsHistoryResponse]]:
    result = await _service.list_history(
        current_user.id,
        page=page,
        page_size=page_size,
    )
    return success(data=result)


@router.post("/claim",
    response_model=APIResponse[PointsClaimResponse],
    summary="领取积分",
    description="""
小程序 **积分** 页面使用。

**使用场景**: 用户完成任务后领取奖励积分

**响应**: 领取结果，含本次领取积分数量

**认证**: 需登录
    """,
)
async def claim_points(
    body: PointsClaimRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[PointsClaimResponse]:
    result = await _service.claim_points(current_user.id, body)
    return success(data=result)


@router.post("/redeem",
    response_model=APIResponse[PointsRedeemResponse],
    summary="兑换积分",
    description="""
小程序 **积分** 页面使用。

**使用场景**: 用户使用积分兑换优惠券或其他权益

**响应**: 兑换结果，含消耗积分数量

**认证**: 需登录
    """,
)
async def redeem_points(
    body: PointsRedeemRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[PointsRedeemResponse]:
    result = await _service.redeem_points(current_user.id, body)
    return success(data=result)