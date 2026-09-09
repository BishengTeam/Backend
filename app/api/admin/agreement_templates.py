from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import require_permission
from app.schemas.admin_agreement_template import (
    AdminAgreementTemplateCreate,
    AdminAgreementTemplateItem,
    AdminAgreementTemplateUpdate,
)
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.admin_agreement_template import AdminAgreementTemplateService

router = APIRouter(prefix="/agreement-templates", tags=["管理后台-协议模板管理"])


@router.get("",
    response_model=APIResponse[PaginatedData[AdminAgreementTemplateItem]],
    summary="协议模板列表",
    description="""
管理后台 **协议模板管理** 页面使用。

**页面路径**: `/admin/agreement-templates`

**使用场景**: 分页查看协议模板（含历史版本），按类型/状态筛选
**查询参数**:
- `type`: 按类型筛选
- `status`: 按状态筛选（active/archived）
**响应**: 分页模板数据，按类型升序、版本倒序
**认证**: 需 `content:list` 权限
    """,
)
async def list_templates(
    type: str | None = Query(None, description="按类型筛选"),
    status: str | None = Query(None, description="按状态筛选"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(require_permission("content:list")),
) -> APIResponse[PaginatedData[AdminAgreementTemplateItem]]:
    """协议模板列表（含历史版本）"""
    result = await AdminAgreementTemplateService().list_templates(
        type, status, page, page_size
    )
    return success(data=result)


@router.post("",
    response_model=APIResponse[AdminAgreementTemplateItem],
    summary="新建协议模板",
    description="""
管理后台 **协议模板管理** 页面使用。

**页面路径**: `/admin/agreement-templates`

**使用场景**: 新建某类型协议模板；自动归档该类型旧生效版本，新模板版本号为历史最大版本 +1
**请求体**: 协议类型、标题、正文
**响应**: 新建的生效模板
**认证**: 需 `content:write` 权限
    """,
)
async def create_template(
    body: AdminAgreementTemplateCreate,
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[AdminAgreementTemplateItem]:
    """新建协议模板（成为该类型唯一生效版本）"""
    result = await AdminAgreementTemplateService().create(body)
    return success(data=result, message="创建成功")


@router.put("/{template_id}",
    response_model=APIResponse[AdminAgreementTemplateItem],
    summary="编辑协议模板（生成新版本）",
    description="""
管理后台 **协议模板管理** 页面使用。

**页面路径**: `/admin/agreement-templates`

**使用场景**: 修改模板标题/正文。保存后旧版本自动归档并生成新版本号；已签署用户保留其签署时内容快照，不受影响
**路径参数**:
- `template_id`: 模板 ID
**请求体**: 新标题、新正文
**响应**: 新版本的生效模板
**认证**: 需 `content:write` 权限
    """,
)
async def update_template(
    template_id: int = Path(..., ge=1, description="模板 ID"),
    body: AdminAgreementTemplateUpdate = ...,
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[AdminAgreementTemplateItem]:
    """编辑模板内容 → 旧版归档 + 新版生效"""
    result = await AdminAgreementTemplateService().update(template_id, body)
    return success(data=result, message="已生成新版本")


@router.put("/{template_id}/archive",
    response_model=APIResponse[AdminAgreementTemplateItem],
    summary="归档协议模板",
    description="""
管理后台 **协议模板管理** 页面使用。

**页面路径**: `/admin/agreement-templates`

**使用场景**: 停用某个生效中的模板；归档后该类型无生效模板，对应业务拦截自动放行，历史签署记录保留
**路径参数**:
- `template_id`: 模板 ID
**响应**: 归档后的模板
**认证**: 需 `content:write` 权限
    """,
)
async def archive_template(
    template_id: int = Path(..., ge=1, description="模板 ID"),
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[AdminAgreementTemplateItem]:
    """归档生效中的模板（仅 active 可归档）"""
    result = await AdminAgreementTemplateService().archive(template_id)
    return success(data=result, message="已归档")
