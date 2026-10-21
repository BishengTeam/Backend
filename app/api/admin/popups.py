from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import require_permission
from app.schemas.admin import AdminBatchDeleteRequest
from app.schemas.admin_popup import PopupCreate, PopupListItem, PopupUpdate
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.admin_popup import AdminPopupService

router = APIRouter(prefix="/popups", tags=["管理后台-弹窗海报"])


@router.get("",
    response_model=APIResponse[PaginatedData[PopupListItem]],
    summary="弹窗海报列表",
    description="""
管理后台 **弹窗海报** 页面使用。

**页面路径**: `/operations/homepage`

**使用场景**: 首页配置-弹窗海报 Tab 加载弹窗列表（含已下架），
支持关键词搜索与分页，按 sort 升序排列。

**查询参数**:
- `keyword`: 按图片地址/跳转链接模糊搜索
- `page`: 页码，从 1 开始
- `page_size`: 每页条数，默认 20，最大 100

**认证**: 需 `homepage:list` 权限
    """,
)
async def list_popups(
    keyword: str | None = Query(None, description="按图片地址/跳转链接模糊搜索"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(require_permission("homepage:list")),
) -> APIResponse[PaginatedData[PopupListItem]]:
    result = await AdminPopupService().list_popups(keyword, page, page_size)
    return success(data=result)


@router.post("",
    response_model=APIResponse[PopupListItem],
    summary="新增弹窗海报",
    description="""
管理后台 **弹窗海报** 页面使用。

**使用场景**: 管理员点击新增按钮，创建登录后弹窗海报。

**请求体**:

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| image_url | str | ✅ | — | 海报图片 URL（建议 1280×1080），最长 512 字符 |
| jump_link | str | 否 | null | 跳转链接：站内页面路径 |
| sort | int | 否 | 0 | 后台列表排序；同时多条 active 时小程序端随机展示其中一条 |
| start_time | str(ISO 8601) | 否 | null | 生效开始时间 |
| end_time | str(ISO 8601) | 否 | null | 生效结束时间 |
| is_active | bool | 否 | true | 是否启用 |

**认证**: 需 `homepage:write` 权限
    """,
)
async def create_popup(
    body: PopupCreate,
    _admin=Depends(require_permission("homepage:write")),
) -> APIResponse[PopupListItem]:
    result = await AdminPopupService().create(body)
    return success(data=result)


@router.put("/{popup_id}",
    response_model=APIResponse[PopupListItem],
    summary="编辑弹窗海报",
    description="""
管理后台 **弹窗海报** 页面使用。

**路径参数**:

| 参数 | 类型 | 说明 |
|------|------|------|
| popup_id | int | Popup ID |

**请求体**: 所有字段可选，仅更新传入的字段（同新增字段，全部改为可选）。

**认证**: 需 `homepage:write` 权限
    """,
)
async def update_popup(
    body: PopupUpdate,
    popup_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("homepage:write")),
) -> APIResponse[PopupListItem]:
    result = await AdminPopupService().update(popup_id, body)
    return success(data=result)


@router.delete("/{popup_id}",
    response_model=APIResponse,
    summary="删除弹窗海报",
    description="""
管理后台 **弹窗海报** 页面使用。

**路径参数**:

| 参数 | 类型 | 说明 |
|------|------|------|
| popup_id | int | Popup ID |

硬删除，数据不可恢复。

**认证**: 需 `homepage:write` 权限
    """,
)
async def delete_popup(
    popup_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("homepage:write")),
):
    await AdminPopupService().delete(popup_id)
    return success(message="弹窗海报已删除")


@router.post("/batch-delete",
    response_model=APIResponse[int],
    summary="批量删除弹窗海报",
    description="""
管理后台 **弹窗海报** 页面使用。

**请求体**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| ids | list[int] | ✅ | Popup ID 列表，至少 1 个 |

**响应**: `data` 为实际删除的条目数量。

**认证**: 需 `homepage:write` 权限
    """,
)
async def batch_delete_popups(
    body: AdminBatchDeleteRequest,
    _admin=Depends(require_permission("homepage:write")),
):
    count = await AdminPopupService().batch_delete(body.ids)
    return success(data=count, message=f"已删除 {count} 个弹窗海报")
