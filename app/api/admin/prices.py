from fastapi import APIRouter, Depends, Path

from app.middleware.auth import require_permission
from app.schemas.admin_price import AdminPriceCreate, AdminPriceUpdate
from app.schemas.common import APIResponse, success
from app.schemas.price_config import PriceResponse
from app.services.admin_price import AdminPriceService

router = APIRouter(prefix="/prices", tags=["管理后台-价格配置"])


@router.post("", response_model=APIResponse[PriceResponse])
async def create_price(
    body: AdminPriceCreate,
    _admin=Depends(require_permission("order:write")),
) -> APIResponse[PriceResponse]:
    result = await AdminPriceService().create(body)
    return success(data=result)


@router.put("/{price_id}", response_model=APIResponse[PriceResponse])
async def update_price(
    body: AdminPriceUpdate,
    price_id: int = Path(..., description="价格配置 ID"),
    _admin=Depends(require_permission("order:write")),
) -> APIResponse[PriceResponse]:
    result = await AdminPriceService().update(price_id, body)
    return success(data=result)


@router.delete("/{price_id}", response_model=APIResponse)
async def delete_price(
    price_id: int = Path(..., description="价格配置 ID"),
    _admin=Depends(require_permission("order:write")),
):
    await AdminPriceService().deactivate(price_id)
    return success(message="价格已停用")
