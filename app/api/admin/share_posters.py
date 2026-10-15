from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import require_permission
from app.schemas.admin import AdminBatchDeleteRequest
from app.schemas.admin_share_poster import SharePosterCreate, SharePosterListItem, SharePosterUpdate
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.admin_share_poster import AdminSharePosterService

router = APIRouter(prefix="/share-posters", tags=["管理后台-分享海报"])


@router.get("",
    response_model=APIResponse[PaginatedData[SharePosterListItem]],
    summary="分享海报列表",
    description="""
管理后台 **分享海报** 页面使用。

**页面路径**: `/operations/homepage`

**使用场景**: 首页配置-分享海报 Tab 加载海报列表（含已下架），按 id 倒序排列。

**查询参数**:
- `page`: 页码，从 1 开始
- `page_size`: 每页条数，默认 20，最大 100

**认证**: 需 `homepage:list` 权限
    """,
)
async def list_share_posters(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(require_permission("homepage:list")),
) -> APIResponse[PaginatedData[SharePosterListItem]]:
    result = await AdminSharePosterService().list_posters(page, page_size)
    return success(data=result)


@router.post("",
    response_model=APIResponse[SharePosterListItem],
    summary="新增分享海报",
    description="""
管理后台 **分享海报** 页面使用。

**使用场景**: 管理员上传推广海报，小程序「分享好友」页展示并可保存到相册。

**请求体**:

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| image_url | str | ✅ | — | 海报图片 URL（建议 1200×1600），最长 512 字符 |
| is_active | bool | 否 | true | 是否启用 |

**认证**: 需 `homepage:write` 权限
    """,
)
async def create_share_poster(
    body: SharePosterCreate,
    _admin=Depends(require_permission("homepage:write")),
) -> APIResponse[SharePosterListItem]:
    result = await AdminSharePosterService().create(body)
    return success(data=result)


@router.put("/{poster_id}",
    response_model=APIResponse[SharePosterListItem],
    summary="编辑分享海报",
    description="""
管理后台 **分享海报** 页面使用。

**路径参数**:

| 参数 | 类型 | 说明 |
|------|------|------|
| poster_id | int | SharePoster ID |

**请求体**: 所有字段可选，仅更新传入的字段（同新增字段）。

**认证**: 需 `homepage:write` 权限
    """,
)
async def update_share_poster(
    body: SharePosterUpdate,
    poster_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("homepage:write")),
) -> APIResponse[SharePosterListItem]:
    result = await AdminSharePosterService().update(poster_id, body)
    return success(data=result)


@router.delete("/{poster_id}",
    response_model=APIResponse,
    summary="删除分享海报",
    description="""
管理后台 **分享海报** 页面使用。

**路径参数**:

| 参数 | 类型 | 说明 |
|------|------|------|
| poster_id | int | SharePoster ID |

硬删除，数据不可恢复。

**认证**: 需 `homepage:write` 权限
    """,
)
async def delete_share_poster(
    poster_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("homepage:write")),
):
    await AdminSharePosterService().delete(poster_id)
    return success(message="分享海报已删除")


@router.post("/batch-delete",
    response_model=APIResponse[int],
    summary="批量删除分享海报",
    description="""
管理后台 **分享海报** 页面使用。

**请求体**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| ids | list[int] | ✅ | SharePoster ID 列表，至少 1 个 |

**响应**: `data` 为实际删除的条目数量。

**认证**: 需 `homepage:write` 权限
    """,
)
async def batch_delete_share_posters(
    body: AdminBatchDeleteRequest,
    _admin=Depends(require_permission("homepage:write")),
):
    count = await AdminSharePosterService().batch_delete(body.ids)
    return success(data=count, message=f"已删除 {count} 个分享海报")
