from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import require_permission
from app.schemas.admin_agreement import AdminAgreementCreate, AdminAgreementListItem, AdminAgreementReview
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.admin_agreement import AdminAgreementService

router = APIRouter(prefix="/agreements", tags=["管理后台-协议管理"])


@router.get("", response_model=APIResponse[PaginatedData[AdminAgreementListItem]])
async def list_agreements(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(require_permission("content:list")),
) -> APIResponse[PaginatedData[AdminAgreementListItem]]:
    result = await AdminAgreementService().list_agreements(page, page_size)
    return success(data=result)


@router.post("", response_model=APIResponse[AdminAgreementListItem])
async def create_agreement(
    body: AdminAgreementCreate,
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[AdminAgreementListItem]:
    result = await AdminAgreementService().create(body)
    return success(data=result)


@router.put("/{agreement_id}/review", response_model=APIResponse[AdminAgreementListItem])
async def review_agreement(
    body: AdminAgreementReview,
    agreement_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[AdminAgreementListItem]:
    result = await AdminAgreementService().review(agreement_id, body)
    return success(data=result)
