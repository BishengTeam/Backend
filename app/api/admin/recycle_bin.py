"""全后台统一回收站 API。

- 列表分「已删除」（30 自然日倒计时）与「已归档」（不倒计时）两个状态。
- 恢复按各模块现有写权限校验；课堂内容对 teacher 按归属隔离。
- 立即彻底删除仅 super_admin + 密码重验（X-Reauth-Token）。
- 已注销用户不进回收站；禁用用户恢复=启用。
"""

from fastapi import APIRouter, Depends, Query

from app.middleware.auth import (
    get_current_admin,
    require_normal_admin,
    require_reauthenticated_super_admin,
)
from app.policy.permissions import ROLE_PERMISSIONS
from app.port.exceptions import ForbiddenException, NotFoundException
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.recycle_bin import (
    RecycleBinItem,
    RecycleBinPurgeRequest,
    RecycleBinRestoreRequest,
    RecycleBinSummaryItem,
)
from app.services.recycle_bin import (
    ARCHIVED_MODULES,
    USER_MODULE_KEY,
    RecycleBinService,
    TrashModuleSpec,
    TRASH_MODULES,
)
from app.domain.user.src.index import User

router = APIRouter(prefix="/recycle-bin", tags=["管理后台-回收站"])

# 用户页签的模块规格（特殊口径：is_active 禁用即"进站"，恢复=启用）
_MODULE_SPECS: dict[str, TrashModuleSpec] = dict(TRASH_MODULES)
_MODULE_SPECS[USER_MODULE_KEY] = TrashModuleSpec(
    USER_MODULE_KEY, "用户", User, None, "user:write"
)


def _visible_module_keys(admin) -> list[str]:
    """按各模块的写权限过滤可见页签（super_admin 全量可见）。"""
    allowed = ROLE_PERMISSIONS.get(admin.role, [])
    if "*" in allowed:
        return [USER_MODULE_KEY, *TRASH_MODULES.keys()]
    keys = [USER_MODULE_KEY] if "user:write" in allowed else []
    for key, spec in TRASH_MODULES.items():
        if spec.restore_permission in allowed:
            keys.append(key)
    return keys


def _ensure_module_visible(admin, module: str) -> TrashModuleSpec:
    spec = _MODULE_SPECS.get(module)
    if spec is None:
        raise NotFoundException("回收站模块")
    if module not in _visible_module_keys(admin):
        raise ForbiddenException(f"缺少权限: {spec.restore_permission}")
    return spec


@router.get("/summary",
    response_model=APIResponse[list[RecycleBinSummaryItem]],
    summary="回收站各模块条目计数",
)
async def recycle_bin_summary(
    admin=Depends(require_normal_admin),
) -> APIResponse[list[RecycleBinSummaryItem]]:
    service = RecycleBinService()
    visible = _visible_module_keys(admin)
    summaries = await service.summary(visible)
    archived_counts = await service.archived_summary()
    for row in summaries:
        row["archived_count"] = archived_counts.get(row["module"], 0)
    return success(
        data=[RecycleBinSummaryItem.model_validate(r) for r in summaries]
    )


@router.get("/items",
    response_model=APIResponse[PaginatedData[RecycleBinItem]],
    summary="回收站条目列表",
    description="""
`state=deleted`（默认，30 自然日倒计时）或 `state=archived`（归档陈列，不倒计时）。
模块可见性按各模块写权限过滤；课堂内容对 teacher 按归属隔离。
    """,
)
async def recycle_bin_items(
    module: str = Query(..., description="模块 key，见 /summary"),
    state: str = Query("deleted", pattern="^(deleted|archived)$"),
    keyword: str | None = Query(None, description="按标题模糊搜索"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin=Depends(require_normal_admin),
) -> APIResponse[PaginatedData[RecycleBinItem]]:
    service = RecycleBinService()
    if state == "archived":
        if module not in ARCHIVED_MODULES:
            raise NotFoundException("归档模块")
        if admin.role != "super_admin" and module not in _visible_module_keys(admin):
            raise ForbiddenException("无权查看该模块")
        result = await service.list_archived(
            module, keyword=keyword, page=page, page_size=page_size
        )
    else:
        spec = _ensure_module_visible(admin, module)
        # 课堂内容按归属隔离：teacher 只能看到自己课堂的条目，super_admin 不限
        teacher_admin_id = (
            admin.id if spec.teacher_scoped and admin.role != "super_admin" else None
        )
        result = await service.list_items(
            spec,
            keyword=keyword,
            page=page,
            page_size=page_size,
            teacher_admin_id=teacher_admin_id,
        )
    return success(data=PaginatedData[RecycleBinItem](**result))


@router.post("/restore",
    response_model=APIResponse[int],
    summary="恢复条目（批量）",
    description="从回收站恢复；按各模块现有写权限校验。过期条目会被逐个拒绝。",
)
async def recycle_bin_restore(
    body: RecycleBinRestoreRequest,
    admin=Depends(require_normal_admin),
) -> APIResponse[int]:
    service = RecycleBinService()
    if body.module in ARCHIVED_MODULES:
        restored = await service.restore_archived(
            body.module, body.ids,
            admin_id=admin.id, is_super_admin=admin.role == "super_admin",
        )
        return success(data=restored, message=f"已恢复 {restored} 条")
    spec = _ensure_module_visible(admin, body.module)
    restored = await service.restore_items(
        spec, body.ids, actor_id=admin.id
    )
    return success(data=restored, message=f"已恢复 {restored} 条")


@router.post("/archive-delete",
    response_model=APIResponse[int],
    summary="归档条目移入回收站（题库）",
    description="原题库管理页归档视图的「移入回收站」入口收编至此：将已归档题库转为删除态，启动 30 自然日倒计时。",
)
async def recycle_bin_archive_delete(
    body: RecycleBinRestoreRequest,
    admin=Depends(require_normal_admin),
) -> APIResponse[int]:
    spec = _ensure_module_visible(admin, body.module)
    deleted = await RecycleBinService().delete_archived(
        body.module, body.ids, admin_id=admin.id
    )
    return success(data=deleted, message=f"已移入回收站 {deleted} 条")


@router.post("/purge",
    response_model=APIResponse[dict],
    summary="立即彻底删除（仅超管）",
    description="""
跳过 30 天等待立即永久删除：无引用物理删，有引用转墓碑（清空内容保留行）。
需 super_admin + 密码重验（X-Reauth-Token，10 分钟内有效）。
    """,
)
async def recycle_bin_purge(
    body: RecycleBinPurgeRequest,
    _admin=Depends(require_reauthenticated_super_admin),
) -> APIResponse[dict]:
    spec = _MODULE_SPECS.get(body.module)
    if spec is None:
        raise NotFoundException("回收站模块")
    result = await RecycleBinService().purge_items(spec, body.ids)
    return success(
        data=result,
        message=f"物理删除 {result['physical']} 条，墓碑化 {result['tombstone']} 条",
    )
