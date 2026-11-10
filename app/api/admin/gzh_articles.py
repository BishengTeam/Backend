from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import require_permission
from app.schemas.admin import AdminBatchDeleteRequest
from app.schemas.admin_gzh_article import (
    GzhArticleBatchImportRequest,
    GzhArticleBatchImportResult,
    GzhArticleCreate,
    GzhArticleListItem,
    GzhArticleUpdate,
)
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.admin_gzh_article import AdminGzhArticleService

router = APIRouter(prefix="/gzh-articles", tags=["管理后台-公众号资讯"])


@router.get("",
    response_model=APIResponse[PaginatedData[GzhArticleListItem]],
    summary="公众号资讯列表",
    description="""
管理后台 **公众号资讯** 页面使用（`/operations/gzh-articles`）。

按发布时间倒序（空值靠后），支持按标题模糊搜索与分页。
已删除（回收站）条目不返回。

**认证**: 需 `homepage:list` 权限
    """,
)
async def list_gzh_articles(
    keyword: str | None = Query(None, description="按标题模糊搜索"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(require_permission("homepage:list")),
) -> APIResponse[PaginatedData[GzhArticleListItem]]:
    result = await AdminGzhArticleService().list_articles(keyword, page, page_size)
    return success(data=result)


@router.post("",
    response_model=APIResponse[GzhArticleListItem],
    summary="手动新增公众号资讯",
    description="""
管理后台 **公众号资讯** 页面使用。

新增 source=manual 的条目（同步 worker 不会覆盖/清理）。
链接已存在时：活跃行报 409；回收站软删行复活并按新内容覆盖。

**认证**: 需 `homepage:write` 权限
    """,
)
async def create_gzh_article(
    body: GzhArticleCreate,
    _admin=Depends(require_permission("homepage:write")),
) -> APIResponse[GzhArticleListItem]:
    result = await AdminGzhArticleService().create(body)
    return success(data=result)


@router.post("/batch-import",
    response_model=APIResponse[GzhArticleBatchImportResult],
    summary="批量导入公众号资讯",
    description="""
管理后台 **公众号资讯** 页面使用（批量粘贴导入）。

逐条导入 source=manual 条目：
- 链接在 payload 内重复或库中已存在（活跃行）→ 跳过
- 链接命中回收站软删行 → 复活并按新内容覆盖（计入 created）

**认证**: 需 `homepage:write` 权限
    """,
)
async def batch_import_gzh_articles(
    body: GzhArticleBatchImportRequest,
    _admin=Depends(require_permission("homepage:write")),
) -> APIResponse[GzhArticleBatchImportResult]:
    result = await AdminGzhArticleService().batch_import(body)
    return success(
        data=result,
        message=f"导入完成：新增 {result.created} 条，跳过 {result.skipped} 条",
    )


@router.put("/{article_id}",
    response_model=APIResponse[GzhArticleListItem],
    summary="编辑公众号资讯",
    description="""
管理后台 **公众号资讯** 页面使用。

仅更新传入字段；保存后条目转为 source=manual（不再被同步 worker 覆盖/清理）。

**认证**: 需 `homepage:write` 权限
    """,
)
async def update_gzh_article(
    body: GzhArticleUpdate,
    article_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("homepage:write")),
) -> APIResponse[GzhArticleListItem]:
    result = await AdminGzhArticleService().update(article_id, body)
    return success(data=result)


@router.delete("/{article_id}",
    response_model=APIResponse,
    summary="删除公众号资讯",
    description="删除后进统一回收站，30 自然日内可恢复。**认证**: 需 `homepage:write` 权限",
)
async def delete_gzh_article(
    article_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("homepage:write")),
):
    await AdminGzhArticleService().delete(article_id)
    return success(message="公众号资讯已删除")


@router.post("/batch-delete",
    response_model=APIResponse[int],
    summary="批量删除公众号资讯",
    description="""
请求体 `ids` 为文章 ID 列表（至少 1 个），响应 `data` 为实际删除条数。
**认证**: 需 `homepage:write` 权限
    """,
)
async def batch_delete_gzh_articles(
    body: AdminBatchDeleteRequest,
    _admin=Depends(require_permission("homepage:write")),
):
    count = await AdminGzhArticleService().batch_delete(body.ids)
    return success(data=count, message=f"已删除 {count} 条公众号资讯")


@router.post("/{article_id}/restore",
    response_model=APIResponse,
    summary="恢复公众号资讯（回收站）",
    description="从回收站恢复，删除后 **30 自然日** 内有效。**认证**: 需 `homepage:write` 权限",
)
async def restore_gzh_article(
    article_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("homepage:write")),
):
    await AdminGzhArticleService().restore(article_id)
    return success(message="公众号资讯已恢复")
