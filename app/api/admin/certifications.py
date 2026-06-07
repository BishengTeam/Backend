from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import PlainTextResponse

from app.middleware.auth import require_permission
from app.schemas.admin_certification import AdminCertificationCreate, AdminCertificationListItem, AdminCertificationUpdate
from app.schemas.certification import CertificationResponse
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.admin_certification import AdminCertificationService
from app.services.certification import CertificationService

router = APIRouter(prefix="/certifications", tags=["管理后台-认证管理"])


@router.get("",
    response_model=APIResponse[PaginatedData[AdminCertificationListItem]],
    summary="认证列表",
    description="""
管理后台 **认证管理** 页面使用。

**页面路径**: `/admin/certification`

**使用场景**: 页面加载时获取认证类型列表，支持关键词搜索

**查询参数**:
- `keyword`: 按认证名称模糊搜索
- `page`: 页码，从 1 开始
- `page_size`: 每页条数，默认 20，最大 100

**响应**: 分页认证数据
    """,
)
async def list_certifications(
    keyword: str | None = Query(None, description="按名称关键词模糊搜索"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(require_permission("content:list")),
):
    """分页查询认证类型列表，支持按名称关键词模糊搜索"""
    result = await AdminCertificationService().list_certifications(keyword, page, page_size)
    return success(data=result)


@router.post("",
    response_model=APIResponse[CertificationResponse],
    summary="新增认证",
    description="""
管理后台 **认证管理** 页面使用。

**页面路径**: `/admin/certification`

**使用场景**: 新增/编辑弹窗提交，创建新认证类型

**请求体**: 认证名称、描述、费用等信息

**响应**: 新创建的认证详情
    """,
)
async def create_certification(
    body: AdminCertificationCreate,
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[CertificationResponse]:
    """创建新认证类型"""
    result = await AdminCertificationService().create(body)
    return success(data=result)


@router.put("/{cert_id}",
    response_model=APIResponse[CertificationResponse],
    summary="编辑认证",
    description="""
管理后台 **认证管理** 页面使用。

**页面路径**: `/admin/certification`

**使用场景**: 新增/编辑弹窗提交，更新已有认证类型信息

**路径参数**:
- `cert_id`: 认证类型 ID

**请求体**: 要更新的认证字段

**响应**: 更新后的认证详情
    """,
)
async def update_certification(
    body: AdminCertificationUpdate,
    cert_id: int = Path(..., description="认证类型 ID"),
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[CertificationResponse]:
    """更新指定认证类型信息"""
    result = await AdminCertificationService().update(cert_id, body)
    return success(data=result)


@router.delete("/{cert_id}",
    response_model=APIResponse,
    summary="下架认证",
    description="""
管理后台 **认证管理** 页面使用。

**页面路径**: `/admin/certification`

**使用场景**: 下架指定认证类型（软删除）

**路径参数**:
- `cert_id`: 认证类型 ID

**响应**: 操作结果消息
    """,
)
async def delete_certification(
    cert_id: int = Path(..., description="认证类型 ID"),
    _admin=Depends(require_permission("content:write")),
):
    """下架指定认证类型"""
    await AdminCertificationService().deactivate(cert_id)
    return success(message="认证已下架")


@router.get("/export",
    response_class=PlainTextResponse,
    summary="导出认证数据",
    description="""
管理后台 **认证管理** 页面使用。

**页面路径**: `/admin/certification`

**使用场景**: 导出所有认证项目数据为 CSV 文件

**响应**: CSV 文件下载
    """,
)
async def export_certifications(
    _admin=Depends(require_permission("content:read")),
):
    """导出认证项目 CSV"""
    csv_content = await CertificationService().export_csv()
    return PlainTextResponse(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=certifications.csv"},
    )