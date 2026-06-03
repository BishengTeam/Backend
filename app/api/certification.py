from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import PlainTextResponse

from app.middleware.auth import get_current_user
from app.models.user import User
from app.schemas.certification import (
    CertificationDetailResponse,
    CertificationFilter,
    CertificationResponse,
    CertTagResponse,
    NispTemplateResponse,
    PinyinResponse,
    SangforCouponResponse,
    Vendor,
    VerifyCodeResponse,
)
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


@router.get("/certifications/tags", response_model=APIResponse[list[CertTagResponse]])
async def certification_tags() -> APIResponse[list[CertTagResponse]]:
    """认证厂商标签列表"""
    return success(data=[
        CertTagResponse(code="H3C", label="H3C"),
        CertTagResponse(code="深信服", label="深信服"),
        CertTagResponse(code="NISP", label="NISP"),
        CertTagResponse(code="人社", label="人社"),
    ])


@router.get("/certifications/{cert_id}", response_model=APIResponse[CertificationDetailResponse])
async def get_certification_detail(
    cert_id: int = Path(..., ge=1, description="认证项目 ID"),
) -> APIResponse[CertificationDetailResponse]:
    """认证详情"""
    result = await CertificationService().get_detail(cert_id)
    return success(data=result)


# ── P2 深信服/NISP ──────────────────────────────────────────────

@router.get("/sangfor/coupons", response_model=APIResponse[list[SangforCouponResponse]])
async def sangfor_coupons(
    current_user: User = Depends(get_current_user),
) -> APIResponse[list[SangforCouponResponse]]:
    """深信服考试券列表"""
    result = await CertificationService().get_sangfor_coupons()
    return success(data=result)


@router.get("/sangfor/verify-code", response_model=APIResponse[VerifyCodeResponse])
async def sangfor_verify_code(
    current_user: User = Depends(get_current_user),
) -> APIResponse[VerifyCodeResponse]:
    """动态验证码下发"""
    code = CertificationService.generate_verify_code()
    return success(data=VerifyCodeResponse(code=code))


@router.get("/nisp/pinyin", response_model=APIResponse[PinyinResponse])
async def nisp_pinyin(
    name: str = Query(..., description="待转换的中文姓名"),
) -> APIResponse[PinyinResponse]:
    """拼音生成"""
    pinyin = CertificationService.convert_to_pinyin(name)
    return success(data=PinyinResponse(pinyin=pinyin))


@router.get("/nisp/template", response_model=APIResponse[NispTemplateResponse])
async def nisp_template() -> APIResponse[NispTemplateResponse]:
    """NISP 模板文件"""
    data = CertificationService.get_nisp_template()
    return success(data=NispTemplateResponse(**data))


@router.get("/export", response_class=PlainTextResponse)
async def export_certifications(
    current_user: User = Depends(get_current_user),
) -> PlainTextResponse:
    """认证报名导出 CSV"""
    csv_content = await CertificationService().export_csv()
    return PlainTextResponse(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=certifications.csv"},
    )