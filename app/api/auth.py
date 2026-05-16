from fastapi import APIRouter

from app.schemas.common import APIResponse, success
from app.schemas.user import LoginRequest, LoginResponse, RefreshRequest, RefreshResponse
from app.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/login")
async def login(body: LoginRequest) -> APIResponse[LoginResponse]:
    """微信 code 登录，返回 JWT token"""
    result = await AuthService().login(body.code)
    return success(data=result)


@router.post("/refresh")
async def refresh_token(body: RefreshRequest) -> APIResponse[RefreshResponse]:
    """刷新 access_token"""
    result = await AuthService().refresh(body.refresh_token)
    return success(data=result)
