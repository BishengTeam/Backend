"""用户端-认证批次查询 API v2。"""
from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapter.database import get_db
from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.schemas.common import APIResponse, success
from app.schemas.plan import UserBatchResponse
from app.services.plan import PlanService

router = APIRouter(prefix="/plans", tags=["认证批次"])


@router.get(
    "",
    response_model=APIResponse[list[UserBatchResponse]],
    summary="产品可报名批次列表",
    description="""
获取指定认证产品当前可报名的批次列表。

批次为跨厂商设计：一个批次可含多个产品，此接口按产品筛选其所属的活跃批次。
返回信息含考试时段、地点、该产品的剩余名额。
""",
)
async def list_plans(
    product_type: str = Query(..., min_length=1, description="认证产品代码，如 H3C-RE"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> APIResponse[list[UserBatchResponse]]:
    result = await PlanService.list_user_batches_for_product(db, product_type)
    return success(data=result)


@router.get(
    "/{plan_id}",
    response_model=APIResponse[UserBatchResponse],
    summary="批次详情",
    description="获取批次完整信息（时段、地点、各产品容量余量）。",
)
async def get_plan(
    plan_id: int = Path(..., description="批次 ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> APIResponse[UserBatchResponse]:
    result = await PlanService.get_user_batch(db, plan_id)
    return success(data=result)
