from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Header, Path, Query, Request

from app.api.admin.error_contracts import admin_error_contract
from app.middleware.auth import (
    require_reauthenticated_super_admin,
    require_super_admin,
)
from app.port.exceptions import BusinessException
from app.schemas.admin_settings import (
    AdminSecurityAuditListItem,
    AdminSettingsLegacyUserUpdate,
    AdminSettingsTemporaryPasswordResponse,
    AdminSettingsUserCreate,
    AdminSettingsUserListItem,
    AdminSettingsUserUpdate,
)
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.admin_security_audit import (
    AdminAuditContext,
    AdminSecurityAuditService,
)
from app.services.admin_settings import AdminSettingsService


router = APIRouter(prefix="/settings", tags=["管理后台-系统设置"])


def _audit_context(request: Request) -> AdminAuditContext:
    return AdminAuditContext(
        request_id=getattr(request.state, "request_id", None),
        source_ip=getattr(request.state, "client_ip", None),
        user_agent=request.headers.get("user-agent"),
    )


def _credential_idempotency_key(
    value: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=16,
        max_length=64,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
) -> str:
    """Require a stable client key for one-time credential operations."""

    return value


@router.get(
    "/admins",
    response_model=APIResponse[PaginatedData[AdminSettingsUserListItem]],
    summary="管理员列表",
    **admin_error_contract("40001", "40100", "40101", "50000"),
)
async def list_admins(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=64),
    role: Literal["super_admin", "quiz_admin", "h3c_admin"] | None = Query(None),
    is_active: bool | None = Query(None),
    is_locked: bool | None = Query(None),
    must_change_password: bool | None = Query(None),
    _admin=Depends(require_super_admin),
) -> APIResponse[PaginatedData[AdminSettingsUserListItem]]:
    result = await AdminSettingsService().list_admins(
        page,
        page_size,
        search=search,
        role=role,
        is_active=is_active,
        is_locked=is_locked,
        must_change_password=must_change_password,
    )
    return success(data=result)


@router.post(
    "/admins",
    response_model=APIResponse[AdminSettingsTemporaryPasswordResponse],
    summary="新增管理员",
    **admin_error_contract(
        "40001", "40100", "40101", "40200", "40201", "50000"
    ),
)
async def create_admin(
    request: Request,
    body: AdminSettingsUserCreate,
    idempotency_key: str = Depends(_credential_idempotency_key),
    admin=Depends(require_reauthenticated_super_admin),
) -> APIResponse[AdminSettingsTemporaryPasswordResponse]:
    result = await AdminSettingsService().create_admin(
        body,
        actor_id=admin.id,
        idempotency_key=idempotency_key,
        context=_audit_context(request),
    )
    return success(data=result, message="管理员已创建")


@router.patch(
    "/admins/{admin_id}",
    response_model=APIResponse[AdminSettingsUserListItem],
    summary="编辑普通管理员显示名",
    **admin_error_contract(
        "40001", "40100", "40101", "40200", "40300", "50000"
    ),
)
async def update_admin(
    request: Request,
    body: AdminSettingsUserUpdate,
    admin_id: int = Path(..., ge=1),
    admin=Depends(require_reauthenticated_super_admin),
) -> APIResponse[AdminSettingsUserListItem]:
    result = await AdminSettingsService().update_admin(
        admin_id,
        body,
        actor_id=admin.id,
        context=_audit_context(request),
    )
    return success(data=result)


@router.post(
    "/admins/{admin_id}/disable",
    response_model=APIResponse[AdminSettingsUserListItem],
    summary="停用普通管理员并失效全部会话",
    **admin_error_contract(
        "40001", "40100", "40101", "40200", "40300", "50000"
    ),
)
async def disable_admin(
    request: Request,
    admin_id: int = Path(..., ge=1),
    admin=Depends(require_reauthenticated_super_admin),
) -> APIResponse[AdminSettingsUserListItem]:
    result = await AdminSettingsService().disable_admin(
        admin_id,
        actor_id=admin.id,
        context=_audit_context(request),
    )
    return success(data=result, message="管理员已停用")


@router.post(
    "/admins/{admin_id}/enable",
    response_model=APIResponse[AdminSettingsTemporaryPasswordResponse],
    summary="使用新临时密码重新启用普通管理员",
    **admin_error_contract(
        "40001",
        "40100",
        "40101",
        "40200",
        "40201",
        "40300",
        "50000",
    ),
)
async def enable_admin(
    request: Request,
    admin_id: int = Path(..., ge=1),
    idempotency_key: str = Depends(_credential_idempotency_key),
    admin=Depends(require_reauthenticated_super_admin),
) -> APIResponse[AdminSettingsTemporaryPasswordResponse]:
    result = await AdminSettingsService().enable_admin(
        admin_id,
        actor_id=admin.id,
        idempotency_key=idempotency_key,
        context=_audit_context(request),
    )
    return success(data=result, message="管理员已重新启用")


@router.post(
    "/admins/{admin_id}/password-reset",
    response_model=APIResponse[AdminSettingsTemporaryPasswordResponse],
    summary="重置普通管理员临时密码",
    **admin_error_contract(
        "40001",
        "40100",
        "40101",
        "40200",
        "40201",
        "40300",
        "50000",
    ),
)
async def reset_admin_password(
    request: Request,
    admin_id: int = Path(..., ge=1),
    idempotency_key: str = Depends(_credential_idempotency_key),
    admin=Depends(require_reauthenticated_super_admin),
) -> APIResponse[AdminSettingsTemporaryPasswordResponse]:
    result = await AdminSettingsService().reset_password(
        admin_id,
        actor_id=admin.id,
        idempotency_key=idempotency_key,
        context=_audit_context(request),
    )
    return success(data=result, message="临时密码已重置")


@router.post(
    "/admins/{admin_id}/unlock",
    response_model=APIResponse[AdminSettingsUserListItem],
    summary="解除普通管理员临时锁定",
    **admin_error_contract(
        "40001", "40100", "40101", "40200", "40300", "50000"
    ),
)
async def unlock_admin(
    request: Request,
    admin_id: int = Path(..., ge=1),
    admin=Depends(require_reauthenticated_super_admin),
) -> APIResponse[AdminSettingsUserListItem]:
    result = await AdminSettingsService().unlock_admin(
        admin_id,
        actor_id=admin.id,
        context=_audit_context(request),
    )
    return success(data=result, message="管理员已解除锁定")


@router.get(
    "/security-audit",
    response_model=APIResponse[PaginatedData[AdminSecurityAuditListItem]],
    summary="管理员安全审计",
    **admin_error_contract("40001", "40100", "40101", "40200", "50000"),
)
async def list_security_audit(
    actor_admin_id: int | None = Query(None, ge=1),
    target_admin_id: int | None = Query(None, ge=1),
    action: str | None = Query(None, max_length=64),
    result: Literal["succeeded", "failed"] | None = Query(None),
    username: str | None = Query(None, max_length=64),
    request_id: str | None = Query(None, max_length=64),
    started_at: datetime | None = Query(None),
    ended_at: datetime | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(require_super_admin),
) -> APIResponse[PaginatedData[AdminSecurityAuditListItem]]:
    result_data = await AdminSecurityAuditService().list_logs(
        actor_admin_id=actor_admin_id,
        target_admin_id=target_admin_id,
        action=action,
        result=result,
        username=username,
        request_id=request_id,
        started_at=started_at,
        ended_at=ended_at,
        page=page,
        page_size=page_size,
    )
    return success(data=result_data)


# Compatibility aliases for the previous settings API.  They use the new
# service invariants and reauthentication gate; no legacy path can create a
# super administrator or bypass session invalidation.
@router.put(
    "/admins/{admin_id}",
    response_model=APIResponse[
        AdminSettingsUserListItem | AdminSettingsTemporaryPasswordResponse
    ],
    summary="编辑管理员（兼容路由）",
    deprecated=True,
    **admin_error_contract(
        "40001",
        "40100",
        "40101",
        "40200",
        "40201",
        "40300",
        "50000",
    ),
)
async def update_admin_legacy(
    request: Request,
    body: AdminSettingsLegacyUserUpdate,
    admin_id: int = Path(..., ge=1),
    idempotency_key: str = Depends(_credential_idempotency_key),
    admin=Depends(require_reauthenticated_super_admin),
):
    supplied = body.model_fields_set
    if len(supplied) != 1:
        raise BusinessException("兼容接口每次只能提交一个变更字段")
    service = AdminSettingsService()
    context = _audit_context(request)
    if "display_name" in supplied and body.display_name is not None:
        result = await service.update_admin(
            admin_id,
            AdminSettingsUserUpdate(display_name=body.display_name),
            actor_id=admin.id,
            context=context,
        )
    elif body.is_active is False:
        result = await service.disable_admin(
            admin_id, actor_id=admin.id, context=context
        )
    elif body.is_active is True:
        result = await service.enable_admin(
            admin_id,
            actor_id=admin.id,
            idempotency_key=idempotency_key,
            context=context,
        )
    else:
        raise BusinessException("未提供有效的管理员变更")
    return success(data=result)


@router.put(
    "/admins/{admin_id}/password",
    response_model=APIResponse[AdminSettingsTemporaryPasswordResponse],
    summary="重置管理员密码（兼容路由）",
    deprecated=True,
    **admin_error_contract(
        "40001",
        "40100",
        "40101",
        "40200",
        "40201",
        "40300",
        "50000",
    ),
)
async def reset_admin_password_legacy(
    request: Request,
    admin_id: int = Path(..., ge=1),
    idempotency_key: str = Depends(_credential_idempotency_key),
    admin=Depends(require_reauthenticated_super_admin),
) -> APIResponse[AdminSettingsTemporaryPasswordResponse]:
    result = await AdminSettingsService().reset_password(
        admin_id,
        actor_id=admin.id,
        idempotency_key=idempotency_key,
        context=_audit_context(request),
    )
    return success(data=result, message="临时密码已重置")
