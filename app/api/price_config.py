from fastapi import APIRouter, Query

from app.schemas.common import APIResponse, success
from app.schemas.price_config import PriceFilter, PriceResponse
from app.services.price_config import PriceConfigService

router = APIRouter(prefix="/prices", tags=["价格配置"])


@router.get("")
async def list_prices(
    cert_type: str | None = Query(None),
    user_type: str | None = Query(None),
) -> APIResponse[list[PriceResponse]]:
    """价格配置列表"""
    filters = PriceFilter(cert_type=cert_type, user_type=user_type) if (cert_type or user_type) else None
    result = await PriceConfigService().list_prices(filters)
    return success(data=result)
