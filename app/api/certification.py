from fastapi import APIRouter, Query

from app.schemas.certification import CertificationFilter, CertificationResponse, Vendor
from app.schemas.common import APIResponse, success
from app.services.certification import CertificationService

router = APIRouter(prefix="/cert", tags=["认证"])


@router.get("/certifications", response_model=APIResponse[list[CertificationResponse]])
async def list_certifications(
    vendor: Vendor | None = Query(None, description="按厂商筛选：H3C / 深信服 / NISP / 人社"),
) -> APIResponse[list[CertificationResponse]]:
    """多认证类型列表（H3C+深信服+NISP+人社）"""
    filters = CertificationFilter(vendor=vendor) if vendor else None
    result = await CertificationService().list_certifications(filters)
    return success(data=result)
