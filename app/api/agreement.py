from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.schemas.agreement import AgreementCreate, AgreementResponse, AgreementSign
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.agreement import AgreementService

router = APIRouter(prefix="/agreements", tags=["协议"])


@router.get("",
    response_model=APIResponse[PaginatedData[AgreementResponse]],
    summary="协议列表",
    description="""
小程序 **培训协议** 页面使用。

**使用场景**: 查看当前用户的培训协议列表，支持按类型筛选和分页
**查询参数**:
- `type`: 按协议类型筛选
- `page`: 页码，从 1 开始
- `page_size`: 每页条数，默认 20，最大 100
**响应**: 分页协议数据，含协议内容、签署状态、签署时间
**认证**: 需登录
    """,
)
async def list_agreements(
    type: str | None = Query(None, description="按协议类型筛选"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[PaginatedData[AgreementResponse]]:
    """当前用户的协议列表，按创建时间倒序"""
    result = await AgreementService().list_agreements(
        current_user.id, type, page, page_size
    )
    return success(data=result)


@router.post("",
    response_model=APIResponse[AgreementResponse],
    summary="新增协议",
    description="""
小程序 **培训协议** 页面使用。

**使用场景**: 为用户创建或关联培训协议（如深信服培训协议）
**请求体**: 协议类型和关联信息
**响应**: 新创建的协议详情
**认证**: 需登录
    """,
)
async def create_agreement(
    body: AgreementCreate,
    current_user: User = Depends(get_current_user),
) -> APIResponse[AgreementResponse]:
    """创建/关联培训协议"""
    result = await AgreementService().create(current_user.id, body)
    return success(data=result)


@router.put("/{agreement_id}/sign",
    response_model=APIResponse[AgreementResponse],
    summary="签署协议",
    description="""
小程序 **培训协议** 页面使用。

**使用场景**: 用户通过 Canvas 签名上传签署协议
**路径参数**:
- `agreement_id`: 协议 ID
**请求体**: 签名图片数据
**响应**: 签署后的协议详情
**认证**: 需登录
    """,
)
async def sign_agreement(
    agreement_id: int = Path(..., description="协议 ID"),
    body: AgreementSign = ...,
    current_user: User = Depends(get_current_user),
) -> APIResponse[AgreementResponse]:
    """Canvas 签名图片上传"""
    result = await AgreementService().sign(current_user.id, agreement_id, body)
    return success(data=result)