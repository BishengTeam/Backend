from fastapi import APIRouter, Depends

from app.middleware.auth import get_current_user_optional
from app.domain.user.src.index import User
from app.schemas.common import APIResponse, success
from app.schemas.zone import HomeAggregationResponse
from app.services.zone import ZoneService

router = APIRouter(prefix="/zones", tags=["专区"])


@router.get("",
    response_model=APIResponse[HomeAggregationResponse],
    summary="首页聚合数据",
    description="""
小程序 **首页** 页面使用。

**使用场景**: 加载首页 Banner + 所有专区类型聚合数据

**响应**: Banner 列表及各专区入口数据

**认证**: 无需登录
    """,
)
async def home_aggregation(
    current_user: User | None = Depends(get_current_user_optional),
) -> APIResponse[HomeAggregationResponse]:
    """首页聚合：Banner + 所有专区类型"""
    result = await ZoneService().get_home_aggregation()
    return success(data=result)
