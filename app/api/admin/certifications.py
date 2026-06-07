from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import PlainTextResponse

from app.middleware.auth import require_permission
from app.schemas.admin_certification import AdminCertificationCreate, AdminCertificationListItem, AdminCertificationUpdate
from app.schemas.certification import CertificationResponse
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.admin_certification import AdminCertificationService
from app.services.certification import CertificationService

router = APIRouter(prefix="/certifications", tags=["管理后台-认证管理"])


@router.get("", response_model=APIResponse[PaginatedData[AdminCertificationListItem]])
async def list_certifications(
    keyword: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(require_permission("content:list")),
):
    result = await AdminCertificationService().list_certifications(keyword, page, page_size)
    return success(data=result)


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


@router.delete("/{cert_id}", response_model=APIResponse)
async def delete_certification(
    cert_id: int = Path(...),
    _admin=Depends(require_permission("content:write")),
):
    await AdminCertificationService().deactivate(cert_id)
    return success(message="认证已下架")


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
