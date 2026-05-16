from fastapi import APIRouter, Depends

from app.middleware.auth import get_current_user
from app.models.user import User
from app.schemas.common import success
from app.schemas.user import (
    PhoneDecryptRequest,
    UserProfile,
    UserProfileUpdate,
)
from app.services.user import UserService

router = APIRouter(prefix="/user", tags=["用户"])


@router.get("/profile", response_model=UserProfile)
async def get_profile(current_user: User = Depends(get_current_user)):
    """获取当前用户资料"""
    return await UserService().get_profile(current_user.id)


@router.put("/profile", response_model=UserProfile)
async def update_profile(
    body: UserProfileUpdate,
    current_user: User = Depends(get_current_user),
):
    """更新用户资料（修改次数限制）"""
    return await UserService().update_profile(current_user.id, body)


@router.delete("/account")
async def delete_account(current_user: User = Depends(get_current_user)):
    """注销账号"""
    await UserService().delete_account(current_user.id)
    return success(message="账号已注销")


@router.post("/phone/decrypt")
async def decrypt_phone(
    body: PhoneDecryptRequest,
    current_user: User = Depends(get_current_user),
):
    """解密微信手机号"""
    phone = await UserService().decrypt_phone(
        current_user.id, body.encrypted_data, body.iv, "re-auth-code"
    )
    return success(data={"phone": phone})
