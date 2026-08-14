"""Super-admin-only deployment and production acceptance API."""

from fastapi import APIRouter, Depends

from app.middleware.auth import require_super_admin
from app.schemas.common import APIResponse, success
from app.schemas.deployment_acceptance import (
    DeploymentAcceptanceResponse,
    DeploymentAcceptanceSignRequest,
)
from app.services.deployment_acceptance import DeploymentAcceptanceService


router = APIRouter(
    prefix="/deployment-acceptance",
    tags=["管理后台-系统部署与验收"],
)


@router.get(
    "",
    response_model=APIResponse[DeploymentAcceptanceResponse],
    summary="查询部署与生产验收状态（仅超级管理员）",
    description=(
        "返回固定发布版本、恢复包摘要、全部必需证据及状态含义；"
        "不返回或修改任何 Secret。"
    ),
)
async def get_deployment_acceptance(
    _admin=Depends(require_super_admin),
) -> APIResponse[DeploymentAcceptanceResponse]:
    return success(data=await DeploymentAcceptanceService().get_status())


@router.post(
    "/accept",
    response_model=APIResponse[DeploymentAcceptanceResponse],
    summary="签署生产验收（仅超级管理员）",
    description=(
        "只有十项系统证据的最新结果全部为 passed，且客户端确认的发布清单"
        "摘要仍与服务器一致时，才允许单向进入 production_accepted。"
    ),
)
async def accept_deployment(
    body: DeploymentAcceptanceSignRequest,
    admin=Depends(require_super_admin),
) -> APIResponse[DeploymentAcceptanceResponse]:
    result = await DeploymentAcceptanceService().accept(
        admin_id=admin.id,
        request=body,
    )
    return success(data=result, message="生产验收已签署")
