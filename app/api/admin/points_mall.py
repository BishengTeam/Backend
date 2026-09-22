from fastapi import APIRouter, Depends, Query

from app.middleware.auth import require_permission
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.points_mall import (
    PointsMallItemCreate,
    PointsMallItemResponse,
    PointsMallItemUpdate,
)
from app.services.points_mall import PointsMallService

router = APIRouter(prefix="/points-mall", tags=["管理后台-积分商城"])
_service = PointsMallService()


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
