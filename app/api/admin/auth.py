from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request

from app.adapter.security import revoke_token
from app.api.admin.error_contracts import admin_error_contract
from app.middleware.auth import get_current_admin, require_super_admin
from app.middleware.rate_limit import limiter
from app.policy.permissions import ROLE_PERMISSIONS
from app.port.exceptions import BusinessException
from app.schemas.admin import (
    AdminChangePasswordRequest,
    AdminInfo,
    AdminLoginRequest,
    AdminLoginResponse,
    AdminMeResponse,
    AdminReauthRequest,
    AdminReauthResponse,
)
from app.schemas.common import APIResponse, success
from app.services.admin_auth import AdminAuthService
from app.services.admin_security_audit import AdminAuditContext


router = APIRouter(prefix="/auth", tags=["管理后台-认证"])


def _audit_context(request: Request) -> AdminAuditContext:
    return AdminAuditContext(
        request_id=getattr(request.state, "request_id", None),
        source_ip=getattr(request.state, "client_ip", None),
        user_agent=request.headers.get("user-agent"),
    )


@router.get(
    "/me",
    response_model=APIResponse[AdminMeResponse],
    summary="获取当前管理员信息",
    **admin_error_contract("40001", "40100", "40202", "50000"),
)
@limiter.limit("30/minute")
async def me(request: Request, admin=Depends(get_current_admin)):
    session_mode = getattr(admin, "_session_mode", "normal")
    return success(
        data=AdminMeResponse(
            admin=AdminInfo.model_validate(admin),
            permissions=ROLE_PERMISSIONS.get(admin.role, []),
            session_mode=session_mode,
            must_change_password=admin.must_change_password,
        )
    )


@router.post(
    "/login",
    response_model=APIResponse[AdminLoginResponse],
    summary="管理员登录",
    **admin_error_contract("40001", "40100", "40202", "50000"),
)
@limiter.limit("5/minute")
async def login(
    request: Request, body: AdminLoginRequest
) -> APIResponse[AdminLoginResponse]:
    result = await AdminAuthService().login(
        body.username,
        body.password,
        context=_audit_context(request),
    )
    return success(data=result)


@router.post(
    "/change-password",
    response_model=APIResponse[None],
    summary="修改当前管理员密码",
    **admin_error_contract("40001", "40100", "40200", "40202", "50000"),
)
@limiter.limit("5/minute")
async def change_password(
    request: Request,
    body: AdminChangePasswordRequest,
    admin=Depends(get_current_admin),
) -> APIResponse[None]:
    await AdminAuthService().change_password(
        admin_id=admin.id,
        expected_auth_version=admin.auth_version,
        current_password=body.current_password,
        new_password=body.new_password,
        context=_audit_context(request),
    )
    return success(message="密码已修改，请重新登录")


@router.post(
    "/reauth",
    response_model=APIResponse[AdminReauthResponse],
    summary="高风险操作前重新验证当前密码",
    **admin_error_contract(
        "40001", "40100", "40101", "40200", "40202", "50000"
    ),
)
@limiter.limit("5/minute")
async def reauthenticate(
    request: Request,
    body: AdminReauthRequest,
    admin=Depends(require_super_admin),
) -> APIResponse[AdminReauthResponse]:
    result = await AdminAuthService().reauthenticate(
        admin_id=admin.id,
        expected_auth_version=admin.auth_version,
        jti=getattr(admin, "_auth_jti", ""),
        password=body.password,
        context=_audit_context(request),
    )
    return success(data=result)


@router.post(
    "/logout",
    response_model=APIResponse[None],
    summary="管理员登出",
    **admin_error_contract("40001", "40100", "40200", "50000"),
)
async def logout(
    request: Request,
    authorization: str | None = Header(None),
    admin=Depends(get_current_admin),
) -> APIResponse[None]:
    token = (authorization or "")[7:]
    revoked = await revoke_token(token)
    await AdminAuthService().record_logout(
        admin_id=admin.id,
        username=admin.username,
        succeeded=revoked,
        context=_audit_context(request),
    )
    if not revoked:
        raise BusinessException("退出登录服务暂不可用，请稍后重试")
    return success(message="已退出登录")
