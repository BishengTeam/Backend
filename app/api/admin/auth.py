from fastapi import APIRouter

from app.schemas.admin import AdminLoginRequest, AdminLoginResponse
from app.schemas.common import APIResponse, success
from app.services.admin_auth import AdminAuthService

router = APIRouter(prefix="/auth", tags=["管理后台-认证"])


@router.post("/login", response_model=APIResponse[AdminLoginResponse])
async def login(body: AdminLoginRequest) -> APIResponse[AdminLoginResponse]:
    result = await AdminAuthService().login(body.username, body.password)
    return success(data=result)
