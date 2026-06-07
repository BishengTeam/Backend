from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
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


@router.get("/certifications",
    response_model=APIResponse[list[CertificationResponse]],
    summary="认证列表",
    description="""
小程序 **认证专区** 页面使用。

**使用场景**: 加载多认证类型列表（H3C+深信服+NISP+人社）

**查询参数**:
- `vendor`: 按厂商筛选（H3C / 深信服 / NISP / 人社）

**认证**: 无需登录
    """,
)
async def list_certifications(
    vendor: Vendor | None = Query(None, description="按厂商筛选：H3C / 深信服 / NISP / 人社"),
) -> APIResponse[list[CertificationResponse]]:
    """多认证类型列表（H3C+深信服+NISP+人社）"""
    filters = CertificationFilter(vendor=vendor) if vendor else None
    result = await CertificationService().list_certifications(filters)
    return success(data=result)


@router.get("/certifications/tags",
    response_model=APIResponse[list[CertTagResponse]],
    summary="认证标签",
    description="""
小程序 **认证专区** 页面使用。

**使用场景**: 获取认证厂商标签列表

**响应**: 标签列表，含 code 和 label

**认证**: 无需登录
    """,
)
async def certification_tags() -> APIResponse[list[CertTagResponse]]:
    """认证厂商标签列表"""
    return success(data=[
        CertTagResponse(code="H3C", label="H3C"),
        CertTagResponse(code="深信服", label="深信服"),
        CertTagResponse(code="NISP", label="NISP"),
        CertTagResponse(code="人社", label="人社"),
    ])


@router.get("/certifications/{cert_id}",
    response_model=APIResponse[CertificationDetailResponse],
    summary="认证详情",
    description="""
小程序 **认证详情** 页面使用。

**使用场景**: 查看认证项目的详细信息

**路径参数**:
- `cert_id`: 认证项目 ID

**认证**: 无需登录
    """,
)
async def get_certification_detail(
    cert_id: int = Path(..., ge=1, description="认证项目 ID"),
) -> APIResponse[CertificationDetailResponse]:
    """认证详情"""
    result = await CertificationService().get_detail(cert_id)
    return success(data=result)


# ── P2 深信服/NISP ──────────────────────────────────────────────

@router.get("/sangfor/coupons",
    response_model=APIResponse[list[SangforCouponResponse]],
    summary="深信服考试券",
    description="""
小程序 **认证详情** 页面使用。

**使用场景**: 获取深信服考试券列表

**响应**: 考试券列表

**认证**: 需登录
    """,
)
async def sangfor_coupons(
    current_user: User = Depends(get_current_user),
) -> APIResponse[list[SangforCouponResponse]]:
    """深信服考试券列表"""
    result = await CertificationService().get_sangfor_coupons()
    return success(data=result)


@router.get("/sangfor/verify-code",
    response_model=APIResponse[VerifyCodeResponse],
    summary="深信服验证码",
    description="""
小程序 **认证详情** 页面使用。

**使用场景**: 动态验证码下发

**响应**: 验证码

**认证**: 需登录
    """,
)
async def sangfor_verify_code(
    current_user: User = Depends(get_current_user),
) -> APIResponse[VerifyCodeResponse]:
    """动态验证码下发"""
    code = CertificationService.generate_verify_code()
    return success(data=VerifyCodeResponse(code=code))


@router.get("/nisp/pinyin",
    response_model=APIResponse[PinyinResponse],
    summary="NISP拼音",
    description="""
小程序 **认证详情** 页面使用。

**使用场景**: 中文姓名转拼音

**查询参数**:
- `name`: 待转换的中文姓名

**认证**: 无需登录
    """,
)
async def nisp_pinyin(
    name: str = Query(..., description="待转换的中文姓名"),
) -> APIResponse[PinyinResponse]:
    """拼音生成"""
    pinyin = CertificationService.convert_to_pinyin(name)
    return success(data=PinyinResponse(pinyin=pinyin))


@router.get("/nisp/template",
    response_model=APIResponse[NispTemplateResponse],
    summary="NISP模板",
    description="""
小程序 **认证详情** 页面使用。

**使用场景**: 获取 NISP 模板文件

**响应**: 模板数据

**认证**: 无需登录
    """,
)
async def nisp_template() -> APIResponse[NispTemplateResponse]:
    """NISP 模板文件"""
    data = CertificationService.get_nisp_template()
    return success(data=NispTemplateResponse(**data))

