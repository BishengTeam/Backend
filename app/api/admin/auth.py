from fastapi import APIRouter, Depends, Header, Request

from app.adapter.security import revoke_token
from app.middleware.auth import get_current_admin
from app.middleware.rate_limit import limiter
from app.policy.permissions import ROLE_PERMISSIONS
from app.schemas.admin import AdminInfo, AdminLoginRequest, AdminLoginResponse, AdminMeResponse
from app.schemas.common import APIResponse, success
from app.services.admin_auth import AdminAuthService

router = APIRouter(prefix="/auth", tags=["管理后台-认证"])


@router.get("/me",
    response_model=APIResponse[AdminMeResponse],
    summary="获取当前管理员信息",
    description="""
管理后台 **登录后** 使用。

**页面路径**: `/admin/login`

**使用场景**: 前端页面刷新或登录后恢复管理员状态，获取当前管理员信息和权限列表
    """,
)
@limiter.limit("30/minute")
async def me(request: Request, admin=Depends(get_current_admin)):
    """返回当前登录管理员的信息和权限列表，供前端刷新时恢复状态"""
    return success(data=AdminMeResponse(
        admin=AdminInfo.model_validate(admin),
        permissions=ROLE_PERMISSIONS.get(admin.role, []),
    ))


@router.post("/login",
    response_model=APIResponse[AdminLoginResponse],
    summary="管理员登录",
    description="""
管理后台 **登录页** 使用。

**页面路径**: `/admin/login`

**使用场景**: 管理员输入用户名密码登录后台管理系统
    """,
)
@limiter.limit("5/minute")
async def login(request: Request, body: AdminLoginRequest) -> APIResponse[AdminLoginResponse]:
    result = await AdminAuthService().login(body.username, body.password)
    return success(data=result)


@router.post("/logout",
    response_model=APIResponse,
    summary="管理员登出",
    description="""
管理后台 **全局** 使用。

**页面路径**: `/admin/login`

**使用场景**: 管理员点击退出登录，将当前 token 加入撤销黑名单
    """,
)
async def logout(authorization: str = Header(...)):
    """退出登录，将 token 加入撤销黑名单"""
    if authorization.startswith("Bearer "):
        token = authorization[7:]
        await revoke_token(token)
    return success(message="已退出登录")
