from fastapi import APIRouter, Depends

from app.middleware.auth import get_current_user
from app.models.user import User
from app.schemas.common import APIResponse, success
from app.schemas.user import (
    PhoneDecryptRequest,
    UserIdentityCreate,
    UserIdentityResponse,
)
from app.services.user import UserService

router = APIRouter(prefix="/user", tags=["用户"])


@router.delete("/account", response_model=APIResponse)
async def delete_account(current_user: User = Depends(get_current_user)):
    """注销账号"""
    await UserService().delete_account(current_user.id)
    return success(message="账号已注销")


@router.post("/phone/decrypt", response_model=APIResponse)
async def decrypt_phone(
    body: PhoneDecryptRequest,
    current_user: User = Depends(get_current_user),
):
    """解密微信手机号"""
    phone = await UserService().decrypt_phone(
        current_user.id, body.encrypted_data, body.iv
    )
    return success(data={"phone": phone})


@router.post("/identity", response_model=APIResponse[UserIdentityResponse])
async def submit_identity(
    body: UserIdentityCreate,
    current_user: User = Depends(get_current_user),
) -> APIResponse[UserIdentityResponse]:
    """提交实名认证信息"""
    result = await UserService().submit_identity(current_user.id, body)
    return success(data=result)


@router.get("/identity", response_model=APIResponse[UserIdentityResponse])
async def get_identity(
    current_user: User = Depends(get_current_user),
) -> APIResponse[UserIdentityResponse]:
    """查询实名认证状态"""
    result = await UserService().get_identity(current_user.id)
    return success(data=result)
