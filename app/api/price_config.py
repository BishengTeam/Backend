from fastapi import APIRouter, Query

from app.schemas.common import APIResponse, success
from app.schemas.price_config import PriceFilter, PriceResponse
from app.services.price_config import PriceConfigService

router = APIRouter(prefix="/prices", tags=["价格配置"])


@router.get("",
    response_model=APIResponse[list[PriceResponse]],
    summary="价格配置列表",
    description="""
小程序 **价格** 相关页面使用。

**使用场景**: 根据认证类型和用户类型获取价格配置列表

**查询参数**:
- `product_type`: 商品类型筛选
- `user_type`: 用户类型筛选

**响应**: 符合条件的价格配置列表

**认证**: 无需登录
    """,
)
async def list_prices(
    product_type: str | None = Query(None),
    user_type: str | None = Query(None),
) -> APIResponse[list[PriceResponse]]:
    """价格配置列表"""
    filters = PriceFilter(product_type=product_type, user_type=user_type) if (product_type or user_type) else None
    result = await PriceConfigService().list_prices(filters)
    return success(data=result)
