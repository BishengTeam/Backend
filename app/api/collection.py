from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import get_current_user
from app.models.user import User
from app.schemas.collection import CollectionCreate, CollectionResponse
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.collection import CollectionService

router = APIRouter(prefix="/collections", tags=["收藏"])


@router.get("", response_model=APIResponse[PaginatedData[CollectionResponse]])
async def list_collections(
    target_type: str | None = Query(None, description="按收藏类型筛选: course / material"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[PaginatedData[CollectionResponse]]:
    """我的收藏列表"""
    result = await CollectionService().list_collections(
        current_user.id,
        target_type=target_type,
        page=page,
        page_size=page_size,
    )
    return success(data=result)


@router.post("", response_model=APIResponse[CollectionResponse])
async def add_collection(
    body: CollectionCreate,
    current_user: User = Depends(get_current_user),
) -> APIResponse[CollectionResponse]:
    """添加收藏"""
    result = await CollectionService().add_collection(current_user.id, body)
    return success(data=result)


@router.delete("/{collection_id}", response_model=APIResponse[None])
async def remove_collection(
    collection_id: int = Path(..., description="收藏记录 ID"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[None]:
    """取消收藏"""
    await CollectionService().remove_collection(current_user.id, collection_id)
    return success()
