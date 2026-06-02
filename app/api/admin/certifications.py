from fastapi import APIRouter, Depends, Path

from app.middleware.auth import require_permission
from app.schemas.admin_certification import AdminCertificationCreate, AdminCertificationUpdate
from app.schemas.certification import CertificationResponse
from app.schemas.common import APIResponse, success
from app.services.admin_certification import AdminCertificationService

router = APIRouter(prefix="/certifications", tags=["管理后台-认证管理"])


@router.post("", response_model=APIResponse[CertificationResponse])
async def create_certification(
    body: AdminCertificationCreate,
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[CertificationResponse]:
    result = await AdminCertificationService().create(body)
    return success(data=result)


@router.put("/{cert_id}", response_model=APIResponse[CertificationResponse])
async def update_certification(
    body: AdminCertificationUpdate,
    cert_id: int = Path(..., description="认证类型 ID"),
    _admin=Depends(require_permission("content:write")),
) -> APIResponse[CertificationResponse]:
    result = await AdminCertificationService().update(cert_id, body)
    return success(data=result)
