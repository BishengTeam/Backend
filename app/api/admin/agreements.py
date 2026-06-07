from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import require_permission
from app.schemas.admin_agreement import AdminAgreementCreate, AdminAgreementListItem, AdminAgreementReview
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.admin_agreement import AdminAgreementService

router = APIRouter(prefix="/agreements", tags=["管理后台-协议管理"])


@router.get("",
    response_model=APIResponse[PaginatedData[AdminAgreementListItem]],
    summary="协议列表",
    description="""
管理后台 **协议管理** 页面使用。

**页面路径**: `/admin/agreements`

**使用场景**: 页面加载时获取协议列表，支持分页

**查询参数**:
- `page`: 页码
- `page_size`: 每页数量

**响应**: 分页协议数据

**认证**: 需 `content:list` 权限
    """,
)
async def list_agreements(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(require_permission("content:list")),
) -> APIResponse[PaginatedData[AdminAgreementListItem]]:
    result = await AdminAgreementService().list_agreements(page, page_size)
    return success(data=result)


@router.post("",
    response_model=APIResponse[AdminAgreementListItem],
    summary="新增协议",
    description="""
管理后台 **协议管理** 页面使用。

**页面路径**: `/admin/agreements`

**使用场景**: 管理员新增一份协议文档

**响应**: 新创建的协议

**认证**: 需 `content:write` 权限
    """,
)
async def create_agreement(
    body: AdminAgreementCreate,
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[AdminAgreementListItem]:
    result = await AdminAgreementService().create(body)
    return success(data=result)


@router.put("/{agreement_id}/review",
    response_model=APIResponse[AdminAgreementListItem],
    summary="审核协议",
    description="""
管理后台 **协议管理** 页面使用。

**页面路径**: `/admin/agreements`

**使用场景**: 管理员审核并更新协议状态

**路径参数**:
- `agreement_id`: 协议 ID

**响应**: 审核后的协议

**认证**: 需 `content:write` 权限
    """,
)
async def review_agreement(
    body: AdminAgreementReview,
    agreement_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[AdminAgreementListItem]:
    result = await AdminAgreementService().review(agreement_id, body)
    return success(data=result)