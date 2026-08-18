from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.admin.error_contracts import admin_error_contract
from app.middleware.auth import require_super_admin
from app.schemas.admin_update import AdminUpdateCheckResult
from app.schemas.common import APIResponse, success
from app.services.admin_update import check_for_update


router = APIRouter(
    prefix="/system/updates",
    tags=["管理后台-系统更新"],
)


@router.get(
    "/check",
    response_model=APIResponse[AdminUpdateCheckResult],
    summary="检查系统更新（仅超级管理员）",
    description=(
        "只读查询 GitHub 最新正式 Release，并与当前运行版本比较；"
        "本接口不执行升级，也不返回任何服务器 Secret。"
    ),
    **admin_error_contract("40100", "40101", "50000"),
)
async def check_update(
    _admin=Depends(require_super_admin),
) -> APIResponse[AdminUpdateCheckResult]:
    return success(data=await check_for_update())
