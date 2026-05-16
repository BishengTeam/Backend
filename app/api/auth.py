from fastapi import APIRouter, Depends

from app.schemas.common import success
from app.schemas.user import LoginRequest, LoginResponse, RefreshRequest, RefreshResponse
from app.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest):
    """微信 code 登录，返回 JWT token"""
    return await AuthService().login(body.code)


@router.post("/refresh", response_model=RefreshResponse)
async def refresh_token(body: RefreshRequest):
    """刷新 access_token"""
    return await AuthService().refresh(body.refresh_token)
