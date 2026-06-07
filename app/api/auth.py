from fastapi import APIRouter, Request

from app.middleware.rate_limit import limiter
from app.schemas.common import APIResponse, success
from app.schemas.user import LoginRequest, LoginResponse, LogoutRequest, RefreshRequest, RefreshResponse
from app.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/login",
    response_model=APIResponse[LoginResponse],
    summary="微信登录",
    description="""
小程序启动时调用。

**使用场景**: 微信 code 登录，返回 JWT token

**请求体**:
- `code`: 微信登录凭证

**响应**: access_token 和 refresh_token

**认证**: 无需登录
    """,
)
@limiter.limit("5/minute")
async def login(request: Request, body: LoginRequest) -> APIResponse[LoginResponse]:
    """微信 code 登录，返回 JWT token"""
    result = await AuthService().login(body.code)
    return success(data=result)


@router.post("/refresh",
    response_model=APIResponse[RefreshResponse],
    summary="刷新token",
    description="""
小程序 token 过期时调用。

**使用场景**: 使用 refresh_token 换取新的 access_token

**请求体**:
- `refresh_token`: 刷新令牌

**认证**: 无需登录
    """,
)
async def refresh_token(body: RefreshRequest) -> APIResponse[RefreshResponse]:
    """刷新 access_token"""
    result = await AuthService().refresh(body.refresh_token)
    return success(data=result)


@router.post("/logout",
    response_model=APIResponse[None],
    summary="登出",
    description="""
小程序用户主动退出时调用。

**使用场景**: 退出登录，删除 refresh_token

**请求体**:
- `refresh_token`: 刷新令牌

**认证**: 无需登录
    """,
)
async def logout(body: LogoutRequest) -> APIResponse[None]:
    """退出登录，删除 refresh_token"""
    await AuthService.logout(body.refresh_token)
    return success(message="已退出登录")