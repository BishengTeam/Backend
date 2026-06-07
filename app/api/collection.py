from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.schemas.collection import CollectionCreate, CollectionResponse
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.collection import CollectionService

router = APIRouter(prefix="/collections", tags=["收藏"])


@router.get("",
    response_model=APIResponse[PaginatedData[CollectionResponse]],
    summary="收藏列表",
    description="""
小程序 **收藏** 页面使用。

**使用场景**: 查看当前用户的收藏列表，支持按类型筛选和分页

**查询参数**:
- `target_type`: 按收藏类型筛选（course / material）
- `page`: 页码
- `page_size`: 每页数量

**响应**: 分页收藏记录

**认证**: 需登录
    """,
)
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


@router.post("",
    response_model=APIResponse[CollectionResponse],
    summary="添加收藏",
    description="""
小程序 **收藏** 页面使用。

**使用场景**: 用户收藏课程或学习资料

**请求体**:
- `target_type`: 收藏类型（course / material）
- `target_id`: 目标 ID

**响应**: 新创建的收藏记录

**认证**: 需登录
    """,
)
async def add_collection(
    body: CollectionCreate,
    current_user: User = Depends(get_current_user),
) -> APIResponse[CollectionResponse]:
    """添加收藏"""
    result = await CollectionService().add_collection(current_user.id, body)
    return success(data=result)


@router.delete("/{collection_id}",
    response_model=APIResponse[None],
    summary="取消收藏",
    description="""
小程序 **收藏** 页面使用。

**使用场景**: 用户取消已收藏的内容

**路径参数**:
- `collection_id`: 收藏记录 ID

**响应**: 无数据返回

**认证**: 需登录
    """,
)
async def remove_collection(
    collection_id: int = Path(..., description="收藏记录 ID"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[None]:
    """取消收藏"""
    await CollectionService().remove_collection(current_user.id, collection_id)
    return success()