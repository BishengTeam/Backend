from fastapi import APIRouter, Depends, Path

from app.middleware.auth import require_normal_admin
from app.adapter.database import get_db_ctx
from app.policy.permissions import ROLE_PERMISSIONS
from app.port.exceptions import ForbiddenException, NotFoundException
from app.schemas.common import APIResponse, success
from app.schemas.consultation_group import (
    ConsultationGroupAdminResponse,
    ConsultationGroupEmptyResponse,
    ConsultationGroupUpdate,
)
from app.services.consultation_group import ConsultationGroupService
from sqlalchemy import select


router = APIRouter(
    prefix="/cert-products/consultation-groups", tags=["管理后台-批次咨询群"]
)
_service = ConsultationGroupService()


async def require_batch_permission(
    plan_id: int = Path(..., ge=1),
    admin=Depends(require_normal_admin),
):
    if admin.role == "super_admin":
        return admin
    async with get_db_ctx() as db:
        permission = await ConsultationGroupService.required_permission(db, plan_id)
    allowed = ROLE_PERMISSIONS.get(admin.role, [])
    if permission not in allowed and "*" not in allowed:
        raise ForbiddenException(f"缺少权限: {permission}")
    return admin


@router.get(
    "/{plan_id}",
    response_model=APIResponse[ConsultationGroupAdminResponse | ConsultationGroupEmptyResponse],
    summary="读取批次咨询群配置",
)
async def get_consultation_group(
    plan_id: int = Path(..., ge=1),
    _admin=Depends(require_batch_permission),
):
    return success(data=await _service.get_admin(plan_id))


@router.put(
    "/{plan_id}",
    response_model=APIResponse[ConsultationGroupAdminResponse | ConsultationGroupEmptyResponse],
    summary="更新批次咨询群配置",
    description="替换或移除当前批次咨询群；使用 version 做并发更新校验。",
)
async def update_consultation_group(
    body: ConsultationGroupUpdate,
    plan_id: int = Path(..., ge=1),
    admin=Depends(require_batch_permission),
):
    return success(
        data=await _service.update(
            plan_id=plan_id,
            admin_id=admin.id,
            data=body,
        )
    )
