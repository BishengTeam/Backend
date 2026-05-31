from fastapi import APIRouter, Request

from app.middleware.rate_limit import limiter
from app.schemas.common import APIResponse, success
from app.schemas.user import LoginRequest, LoginResponse, LogoutRequest, RefreshRequest, RefreshResponse
from app.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/login", response_model=APIResponse[LoginResponse])
@limiter.limit("5/minute")
async def login(request: Request, body: LoginRequest) -> APIResponse[LoginResponse]:
    """微信 code 登录，返回 JWT token"""
    result = await AuthService().login(body.code)
    return success(data=result)


@router.post("/refresh", response_model=APIResponse[RefreshResponse])
async def refresh_token(body: RefreshRequest) -> APIResponse[RefreshResponse]:
    """刷新 access_token"""
    result = await AuthService().refresh(body.refresh_token)
    return success(data=result)


@router.post("/logout")
async def logout(body: LogoutRequest):
    """退出登录，删除 refresh_token"""
    await AuthService.logout(body.refresh_token)
    return success(message="已退出登录")
