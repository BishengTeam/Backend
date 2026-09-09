from fastapi import APIRouter, Query

from app.schemas.agreement_template import AgreementTemplatePublic
from app.schemas.common import APIResponse, success
from app.services.agreement_template import AgreementTemplateService

router = APIRouter(prefix="/agreement-templates", tags=["协议"])


@router.get("",
    response_model=APIResponse[AgreementTemplatePublic],
    summary="获取当前生效协议模板",
    description="""
小程序 **登录协议 / 实名授权** 页面使用。

**使用场景**: 展示当前生效版本的协议全文（登录页未登录也可访问）
**查询参数**:
- `type`: 协议类型：`user_terms` / `privacy` / `identity_auth`
**响应**: 模板标题、正文全文、版本号
**认证**: 无需登录
    """,
)
async def get_active_template(
    type: str = Query(..., description="协议类型：user_terms/privacy/identity_auth"),
) -> APIResponse[AgreementTemplatePublic]:
    """当前生效的协议模板（未配置时返回 404 业务错误）"""
    result = await AgreementTemplateService().get_active(type)
    return success(data=result)
