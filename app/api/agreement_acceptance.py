from fastapi import APIRouter, Depends

from app.domain.user.src.index import User
from app.middleware.auth import get_current_user
from app.schemas.agreement_template import (
    AgreementAcceptRequest,
    AgreementAcceptanceItem,
)
from app.schemas.common import APIResponse, success
from app.services.agreement_template import AgreementTemplateService

router = APIRouter(prefix="/agreement-acceptances", tags=["协议"])


@router.post("",
    response_model=APIResponse[list[AgreementAcceptanceItem]],
    summary="签署协议（记录同意）",
    description="""
小程序 **登录** 与 **实名认证** 页面使用。

**使用场景**: 用户勾选同意后记录签署（登录时一次提交 user_terms + privacy；实名认证提交前提交 identity_auth）
**请求体**: `items` 数组，每项含 `type` 与签署时基于的 `version`
**响应**: 签署记录列表（重复提交同一版本幂等）
**认证**: 需登录
    """,
)
async def accept_agreements(
    body: AgreementAcceptRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[list[AgreementAcceptanceItem]]:
    result = await AgreementTemplateService().accept(current_user.id, body)
    return success(data=result, message="签署成功")


@router.get("",
    response_model=APIResponse[list[AgreementAcceptanceItem]],
    summary="我的协议签署记录",
    description="""
小程序 **我的-我的协议** 页面使用。

**使用场景**: 展示当前用户已签署的协议记录（类型、标题、版本、签署时间）
**响应**: 签署记录列表，按签署时间倒序
**认证**: 需登录
    """,
)
async def list_my_acceptances(
    current_user: User = Depends(get_current_user),
) -> APIResponse[list[AgreementAcceptanceItem]]:
    result = await AgreementTemplateService().list_acceptances(current_user.id)
    return success(data=result)
