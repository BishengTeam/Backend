from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.schemas.certification import CertificationFilter, CertificationResponse, XuexinGuideResponse
from app.services.certification import CertificationService

router = APIRouter(prefix="/cert", tags=["认证"])


@router.get("/certifications", response_model=list[CertificationResponse])
async def list_certifications(
    vendor: str | None = Query(None),
):
    """多认证类型列表（H3C+深信服+NISP+人社）"""
    filters = CertificationFilter(vendor=vendor) if vendor else None
    return await CertificationService().list_certifications(filters)


@router.get("/export")
async def export_certifications():
    """报名信息导出 CSV"""
    filename, content = await CertificationService().export_certifications()
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/xuexin", response_model=XuexinGuideResponse)
async def xuexin_guide():
    """学信网验证引导"""
    return CertificationService.get_xuexin_guide()
