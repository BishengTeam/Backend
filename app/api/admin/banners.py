from fastapi import APIRouter, Depends, Path

from app.middleware.auth import require_permission
from app.schemas.admin import AdminBatchDeleteRequest
from app.schemas.admin_banner import BannerCreate, BannerListItem, BannerUpdate
from app.schemas.common import APIResponse, success
from app.services.admin_banner import AdminBannerService

router = APIRouter(prefix="/banners", tags=["管理后台-Banner管理"])


@router.get("", response_model=APIResponse[list[BannerListItem]])
async def list_banners(
    _admin=Depends(require_permission("content:list")),
) -> APIResponse[list[BannerListItem]]:
    result = await AdminBannerService().list_banners()
    return success(data=result)


@router.post("", response_model=APIResponse[BannerListItem])
async def create_banner(
    body: BannerCreate,
    _admin=Depends(require_permission("content:banner")),
) -> APIResponse[BannerListItem]:
    result = await AdminBannerService().create(body)
    return success(data=result)


@router.put("/{banner_id}", response_model=APIResponse[BannerListItem])
async def update_banner(
    body: BannerUpdate,
    banner_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("content:banner")),
) -> APIResponse[BannerListItem]:
    result = await AdminBannerService().update(banner_id, body)
    return success(data=result)


@router.delete("/{banner_id}", response_model=APIResponse)
async def delete_banner(
    banner_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("content:banner")),
):
    await AdminBannerService().delete(banner_id)
    return success(message="Banner 已删除")


@router.post("/batch-delete", response_model=APIResponse[int])
async def batch_delete_banners(
    body: AdminBatchDeleteRequest,
    _admin=Depends(require_permission("content:banner")),
):
    count = await AdminBannerService().batch_delete(body.ids)
    return success(data=count, message=f"已删除 {count} 个 Banner")
