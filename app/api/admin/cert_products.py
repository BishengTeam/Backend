from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import require_permission
from app.schemas.admin_cert_product import (
    CertProductCreate,
    CertProductResponse,
    CertProductStats,
    CertProductUpdate,
)
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.admin_cert_product import AdminCertProductService

router = APIRouter(prefix="/cert-products", tags=["管理后台-认证产品"])


@router.get(
    "/stats",
    response_model=APIResponse[list[CertProductStats]],
    summary="认证产品聚合统计",
    description="""
管理后台 **认证产品** 页面使用。

**使用场景**: 页面顶部统计卡片，按认证类型（h3c/renshe）分组展示产品数、活跃批次数、报名总数。

**响应**: 按 type 分组的统计列表
    """,
)
async def get_stats(
    _admin=Depends(require_permission("content:read")),
) -> APIResponse[list[CertProductStats]]:
    """获取认证产品聚合统计数据"""
    result = await AdminCertProductService().get_stats()
    return success(data=result)


@router.get(
    "",
    response_model=APIResponse[PaginatedData[CertProductResponse]],
    summary="认证产品列表",
    description="""
管理后台 **认证产品** 页面使用。

**使用场景**: 页面加载时获取认证产品列表，支持按类型筛选和关键词搜索。

**查询参数**:
- `type`: 认证类型筛选（h3c / renshe）
- `keyword`: 按产品编码、英文名或中文名模糊搜索
- `page`: 页码，从 1 开始
- `page_size`: 每页条数，默认 20，最大 100

**响应**: 分页认证产品数据
    """,
)
async def list_products(
    type: str | None = Query(None, description="认证类型筛选：h3c / renshe"),
    keyword: str | None = Query(None, description="按编码/名称关键词模糊搜索"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(require_permission("content:list")),
) -> APIResponse[PaginatedData[CertProductResponse]]:
    """分页查询认证产品列表，支持按类型筛选和关键词搜索"""
    result = await AdminCertProductService().list_products(type, keyword, page, page_size)
    return success(data=result)


@router.post(
    "",
    response_model=APIResponse[CertProductResponse],
    summary="新增认证产品",
    description="""
管理后台 **认证产品** 页面使用。

**使用场景**: 新增/编辑弹窗提交，创建新的认证产品。

**请求体**: 认证类型、编码、名称、描述等。

**响应**: 新创建的认证产品详情
    """,
)
async def create_product(
    body: CertProductCreate,
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[CertProductResponse]:
    """创建新认证产品"""
    result = await AdminCertProductService().create(body)
    return success(data=result)


@router.get(
    "/{code}",
    response_model=APIResponse[CertProductResponse],
    summary="认证产品详情",
    description="""
管理后台 **认证产品** 页面使用。

**使用场景**: 编辑弹窗加载时获取单个认证产品详情。

**路径参数**:
- `code`: 产品编码，如 H3CNE

**响应**: 认证产品详情
    """,
)
async def get_product(
    code: str = Path(..., description="产品编码"),
    _admin=Depends(require_permission("content:read")),
) -> APIResponse[CertProductResponse]:
    """获取指定编码的认证产品详情"""
    result = await AdminCertProductService().get_by_code(code)
    return success(data=result)


@router.put(
    "/{code}",
    response_model=APIResponse[CertProductResponse],
    summary="编辑认证产品",
    description="""
管理后台 **认证产品** 页面使用。

**使用场景**: 新增/编辑弹窗提交，更新已有认证产品信息。

**路径参数**:
- `code`: 产品编码

**请求体**: 要更新的字段

**响应**: 更新后的认证产品详情
    """,
)
async def update_product(
    body: CertProductUpdate,
    code: str = Path(..., description="产品编码"),
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[CertProductResponse]:
    """更新指定编码的认证产品信息"""
    result = await AdminCertProductService().update(code, body)
    return success(data=result)


@router.delete(
    "/{code}",
    response_model=APIResponse,
    summary="下架认证产品",
    description="""
管理后台 **认证产品** 页面使用。

**使用场景**: 下架指定认证产品（软删除）。

**路径参数**:
- `code`: 产品编码

**响应**: 操作结果消息
    """,
)
async def deactivate_product(
    code: str = Path(..., description="产品编码"),
    _admin=Depends(require_permission("content:write")),
):
    """下架指定认证产品（软删除）"""
    await AdminCertProductService().deactivate(code)
    return success(message="认证产品已下架")
