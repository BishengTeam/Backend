from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.schemas.agreement import AgreementCreate, AgreementResponse, AgreementSign
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.agreement import AgreementService

router = APIRouter(prefix="/agreements", tags=["协议"])


@router.get("", response_model=APIResponse[PaginatedData[AgreementResponse]])
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


@router.post("", response_model=APIResponse[AgreementResponse])
async def create_agreement(
    body: AgreementCreate,
    current_user: User = Depends(get_current_user),
) -> APIResponse[AgreementResponse]:
    """创建/关联培训协议"""
    result = await AgreementService().create(current_user.id, body)
    return success(data=result)


@router.put("/{agreement_id}/sign", response_model=APIResponse[AgreementResponse])
async def sign_agreement(
    agreement_id: int = Path(..., description="协议 ID"),
    body: AgreementSign = ...,
    current_user: User = Depends(get_current_user),
) -> APIResponse[AgreementResponse]:
    """Canvas 签名图片上传"""
    result = await AgreementService().sign(current_user.id, agreement_id, body)
    return success(data=result)