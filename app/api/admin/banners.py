from fastapi import APIRouter, Depends, Path

from app.middleware.auth import require_permission
from app.schemas.admin import AdminBatchDeleteRequest
from app.schemas.admin_banner import BannerCreate, BannerListItem, BannerUpdate
from app.schemas.common import APIResponse, success
from app.services.admin_banner import AdminBannerService

router = APIRouter(prefix="/banners", tags=["管理后台-Banner管理"])


@router.get("",
    response_model=APIResponse[list[BannerListItem]],
    summary="Banner 列表",
    description="""
管理后台 **Banner 管理** 页面使用。

**页面路径**: `/admin/banners`

**使用场景**: 页面加载时获取所有 Banner（含已下架），按 sort 升序排列。

**认证**: 需 `homepage:list` 权限

**响应字段**:

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int | Banner ID |
| image_url | str | 图片 URL |
| jump_link | str | 跳转链接（target_type=url 时生效） |
| target_type | str | 跳转资源类型：`cert` / `course` / `activity` / `zone` / `url` |
| target_id | int | 跳转资源 ID（target_type=url 时为空） |
| sort | int | 排序权重，越小越靠前 |
| start_time | str(ISO 8601) | 生效开始时间，空表示不限 |
| end_time | str(ISO 8601) | 生效结束时间，空表示不限 |
| is_active | bool | 是否启用 |
| created_at | str(ISO 8601) | 创建时间 |
    """,
)
async def list_banners(
    _admin=Depends(require_permission("homepage:list")),
) -> APIResponse[list[BannerListItem]]:
    result = await AdminBannerService().list_banners()
    return success(data=result)


@router.post("",
    response_model=APIResponse[BannerListItem],
    summary="新增 Banner",
    description="""
管理后台 **Banner 管理** 页面使用。

**页面路径**: `/admin/banners`

**使用场景**: 管理员点击新增按钮，创建新的 Banner 轮播图。

**请求体**:

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| image_url | str | ✅ | — | Banner 图片 URL，最长 512 字符 |
| jump_link | str | 否 | null | 跳转链接（target_type=url 时生效） |
| target_type | str | 否 | null | 跳转资源类型：`cert` / `course` / `activity` / `zone` / `url` |
| target_id | int | 否 | null | 跳转资源 ID（target_type=url 时为空），≥1 |
| sort | int | 否 | 0 | 排序权重 |
| start_time | str(ISO 8601) | 否 | null | 生效开始时间 |
| end_time | str(ISO 8601) | 否 | null | 生效结束时间 |
| is_active | bool | 否 | true | 是否启用 |

**跳转规则**: 前端优先使用 `target_type` + `target_id` 拼接页面路径；仅当 `target_type="url"` 时使用 `jump_link` 直接跳转。

**认证**: 需 `content:banner` 权限
    """,
)
async def create_banner(
    body: BannerCreate,
    _admin=Depends(require_permission("homepage:write")),
) -> APIResponse[BannerListItem]:
    result = await AdminBannerService().create(body)
    return success(data=result)


@router.put("/{banner_id}",
    response_model=APIResponse[BannerListItem],
    summary="编辑 Banner",
    description="""
管理后台 **Banner 管理** 页面使用。

**页面路径**: `/admin/banners`

**使用场景**: 管理员在列表中编辑已有 Banner。

**路径参数**:

| 参数 | 类型 | 说明 |
|------|------|------|
| banner_id | int | Banner ID |

**请求体**: 所有字段可选，仅更新传入的字段（同新增字段，全部改为可选）。

**认证**: 需 `content:banner` 权限
    """,
)
async def update_banner(
    body: BannerUpdate,
    banner_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("homepage:write")),
) -> APIResponse[BannerListItem]:
    result = await AdminBannerService().update(banner_id, body)
    return success(data=result)


@router.delete("/{banner_id}",
    response_model=APIResponse,
    summary="删除 Banner",
    description="""
管理后台 **Banner 管理** 页面使用。

**页面路径**: `/admin/banners`

**使用场景**: 管理员删除指定 Banner（硬删除，数据不可恢复）。

**路径参数**:

| 参数 | 类型 | 说明 |
|------|------|------|
| banner_id | int | Banner ID |

**认证**: 需 `content:banner` 权限
    """,
)
async def delete_banner(
    banner_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("homepage:write")),
):
    await AdminBannerService().delete(banner_id)
    return success(message="Banner 已删除")


@router.post("/batch-delete",
    response_model=APIResponse[int],
    summary="批量删除 Banner",
    description="""
管理后台 **Banner 管理** 页面使用。

**页面路径**: `/admin/banners`

**使用场景**: 管理员勾选多条 Banner 后批量删除。

**请求体**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| ids | list[int] | ✅ | Banner ID 列表，至少 1 个 |

**响应**: `data` 为实际删除的条目数量。

**认证**: 需 `content:banner` 权限
    """,
)
async def batch_delete_banners(
    body: AdminBatchDeleteRequest,
    _admin=Depends(require_permission("homepage:write")),
):
    count = await AdminBannerService().batch_delete(body.ids)
    return success(data=count, message=f"已删除 {count} 个 Banner")
