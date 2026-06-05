from fastapi import APIRouter, Depends, Path
from fastapi.responses import PlainTextResponse

from app.middleware.auth import require_permission
from app.schemas.admin_certification import AdminCertificationCreate, AdminCertificationUpdate
from app.schemas.certification import CertificationResponse
from app.schemas.common import APIResponse, success
from app.services.admin_certification import AdminCertificationService
from app.services.certification import CertificationService

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


@router.get("/export", response_class=PlainTextResponse)
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
