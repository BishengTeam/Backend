from fastapi import APIRouter, Depends, Query
from app.middleware.auth import require_reauthenticated_admin

from app.middleware.auth import require_permission
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.points_mall import (
    PointsMallItemCreate,
    PointsMallItemResponse,
    PointsMallItemUpdate,
)
from app.schemas.points_virtual import (
    PointsVirtualOrderResponse,
    PointsVirtualProductCreate,
    PointsVirtualProductResponse,
    PointsVirtualProductUpdate,
    PointsVirtualRefundRequest,
    PointsVirtualRefundResponse,
)
from app.services.points_mall import PointsMallService
from app.services.points_virtual import PointsVirtualService

router = APIRouter(prefix="/points-mall", tags=["管理后台-积分商城"])
_service = PointsMallService()
_virtual_service = PointsVirtualService()


@router.get("/virtual-products")
async def list_virtual_products(
    status: str | None = Query(None),
    product_type: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(require_permission("points:manage")),
):
    return success(
        data=await _virtual_service.admin_list_products(
            status=status,
            product_type=product_type,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/virtual-products", response_model=APIResponse[PointsVirtualProductResponse])
async def create_virtual_product(
    body: PointsVirtualProductCreate,
    _admin=Depends(require_permission("points:manage")),
):
    return success(data=await _virtual_service.admin_create_product(body))


@router.put(
    "/virtual-products/{product_id}",
    response_model=APIResponse[PointsVirtualProductResponse],
)
async def update_virtual_product(
    product_id: int,
    body: PointsVirtualProductUpdate,
    _admin=Depends(require_permission("points:manage")),
):
    return success(
        data=await _virtual_service.admin_update_product(product_id, body)
    )


@router.get(
    "/virtual-orders",
    response_model=APIResponse[PaginatedData[PointsVirtualOrderResponse]],
)
async def list_virtual_orders(
    user_id: int | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(require_permission("points:manage")),
):
    return success(
        data=await _virtual_service.admin_list_orders(
            user_id=user_id,
            status=status,
            page=page,
            page_size=page_size,
        )
    )


@router.post(
    "/virtual-orders/{order_id}/refund",
    response_model=APIResponse[PointsVirtualRefundResponse],
)
async def refund_virtual_order(
    order_id: int,
    body: PointsVirtualRefundRequest,
    _admin=Depends(require_permission("points:manage")),
    reauth_admin=Depends(require_reauthenticated_admin),
):
    return success(
        data=await _virtual_service.refund(
            admin_id=reauth_admin.id,
            order_id=order_id,
            data=body,
        )
    )


@router.get("/items", response_model=APIResponse[PaginatedData[PointsMallItemResponse]])
async def list_items(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin = Depends(require_permission("points:manage")),
) -> APIResponse[PaginatedData[PointsMallItemResponse]]:
    items, total = await _service.admin_list(page=page, page_size=page_size)
    return success(data=PaginatedData(items=items, total=total, page=page, page_size=page_size))


@router.post("/items", response_model=APIResponse[PointsMallItemResponse])
async def create_item(
    body: PointsMallItemCreate,
    _admin = Depends(require_permission("points:manage")),
) -> APIResponse[PointsMallItemResponse]:
    return success(data=await _service.admin_create(body))


@router.put("/items/{item_id}", response_model=APIResponse[PointsMallItemResponse])
async def update_item(
    item_id: int,
    body: PointsMallItemUpdate,
    _admin = Depends(require_permission("points:manage")),
) -> APIResponse[PointsMallItemResponse]:
    return success(data=await _service.admin_update(item_id, body))


@router.delete("/items/{item_id}", response_model=APIResponse[None])
async def delete_item(
    item_id: int,
    _admin = Depends(require_permission("points:manage")),
) -> APIResponse[None]:
    await _service.admin_delete(item_id)
    return success(data=None)
