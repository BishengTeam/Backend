from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.schemas.certification import CertificationFilter, CertificationResponse, XuexinGuideResponse
from app.schemas.common import APIResponse, success
from app.services.certification import CertificationService

router = APIRouter(prefix="/cert", tags=["认证"])


@router.get("/certifications")
async def list_certifications(
    vendor: str | None = Query(None),
) -> APIResponse[list[CertificationResponse]]:
    """多认证类型列表（H3C+深信服+NISP+人社）"""
    filters = CertificationFilter(vendor=vendor) if vendor else None
    result = await CertificationService().list_certifications(filters)
    return success(data=result)


@router.get("/export")
async def export_certifications():
    """报名信息导出 CSV"""
    filename, content = await CertificationService().export_certifications()
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/xuexin")
async def xuexin_guide() -> APIResponse[XuexinGuideResponse]:
    """学信网验证引导"""
    result = CertificationService.get_xuexin_guide()
    return success(data=result)
