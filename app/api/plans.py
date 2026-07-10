from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.schemas.common import APIResponse, success
from app.schemas.plan import PlanResponse
from app.services.plan import PlanService

router = APIRouter(prefix="/plans", tags=["批次"])


@router.get("",
    response_model=APIResponse[list[PlanResponse]],
    summary="可报名的批次列表",
    description="""
获取某认证产品下已发布的批次列表。

**使用场景**: 用户进入 H3C/NISP/深信服/人社 报名页时，选择要报名的批次。

**查询参数**:
- `product_type`: 认证产品代码，如 H3C-RE

**认证**: 需登录
    """,
)
async def list_plans(
    product_type: str = Query(..., min_length=1, description="认证产品代码"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[list[PlanResponse]]:
    result = await PlanService().list_published_plans(product_type)
    return success(data=result)


@router.get("/{plan_id}",
    response_model=APIResponse[PlanResponse],
    summary="批次详情",
    description="""
获取指定批次的详细信息（含剩余名额）。

**认证**: 需登录
    """,
)
async def get_plan(
    plan_id: int = Path(..., description="批次 ID"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[PlanResponse]:
    result = await PlanService().get_plan(plan_id)
    return success(data=result)
