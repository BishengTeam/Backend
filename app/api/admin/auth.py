from fastapi import APIRouter, Depends, Request

from app.middleware.auth import get_current_admin
from app.middleware.rate_limit import limiter
from app.schemas.admin import ROLE_PERMISSIONS, AdminInfo, AdminLoginRequest, AdminLoginResponse, AdminMeResponse
from app.schemas.common import APIResponse, success
from app.services.admin_auth import AdminAuthService

router = APIRouter(prefix="/auth", tags=["管理后台-认证"])


@router.get("/me", response_model=APIResponse[AdminMeResponse])
@limiter.limit("30/minute")
async def me(request: Request, admin=Depends(get_current_admin)):
    """返回当前登录管理员的信息和权限列表，供前端刷新时恢复状态"""
    return success(data=AdminMeResponse(
        admin=AdminInfo.model_validate(admin),
        permissions=ROLE_PERMISSIONS.get(admin.role, []),
    ))


@router.post("/login", response_model=APIResponse[AdminLoginResponse])
@limiter.limit("5/minute")
async def login(request: Request, body: AdminLoginRequest) -> APIResponse[AdminLoginResponse]:
    result = await AdminAuthService().login(body.username, body.password)
    return success(data=result)


@router.post("/logout", response_model=APIResponse)
async def logout():
    """退出登录（JWT 无状态，客户端自行清除 token）"""
    return success(message="已退出登录")
