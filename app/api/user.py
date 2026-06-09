from fastapi import APIRouter, Depends

from app.middleware.auth import get_current_user
from app.schemas.common import APIResponse, success
from app.schemas.user import (
    EnterpriseResponse,
    EnterpriseSubmit,
    LoginResponse,
    LogoutRequest,
    PhoneDecryptRequest,
    RealnameResponse,
    RealnameSubmit,
    StudentResponse,
    StudentSubmit,
    UserProfileDetail,
    UserProfileUpdate,
    UserUnbindRequest,
)
from app.services.user import UserService

router = APIRouter(prefix="/user", tags=["用户端-用户信息"])


# ═══════ Level 1: 基础资料 ═══════

@router.get("/profile",
    response_model=APIResponse[UserProfileDetail],
    summary="获取个人信息",
)
async def get_profile(current_user=Depends(get_current_user)):
    result = await UserService().get_profile(current_user.id)
    return success(data=result)


@router.put("/profile",
    response_model=APIResponse[UserProfileDetail],
    summary="编辑个人信息（Level 1：无需审核）",
)
async def update_profile(
    body: UserProfileUpdate,
    current_user=Depends(get_current_user),
):
    result = await UserService().update_profile(current_user.id, body)
    return success(data=result)


@router.post("/phone/decrypt",
    response_model=APIResponse,
    summary="解密手机号",
)
async def decrypt_phone(body: PhoneDecryptRequest, current_user=Depends(get_current_user)):
    phone = await UserService().decrypt_phone(
        current_user.id, body.encrypted_data, body.iv
    )
    return success(data={"phone": phone}, message="手机号解密成功")


@router.post("/unbind",
    response_model=APIResponse,
    summary="解绑账号",
)
async def unbind(body: UserUnbindRequest, current_user=Depends(get_current_user)):
    await UserService().unbind(current_user.id, body.type)
    return success(message="解绑成功")


# ═══════ Level 2: 实名认证 ═══════

@router.post("/identity",
    response_model=APIResponse[RealnameResponse],
    summary="提交实名认证（Level 2：需审核）",
)
async def submit_identity(
    body: RealnameSubmit,
    current_user=Depends(get_current_user),
):
    result = await UserService().submit_realname(current_user.id, body)
    return success(data=result)


@router.get("/identity",
    response_model=APIResponse[RealnameResponse],
    summary="查看实名认证信息",
)
async def get_identity(current_user=Depends(get_current_user)):
    result = await UserService().get_realname(current_user.id)
    return success(data=result)


# ═══════ Level 2: 学生信息 ═══════

@router.post("/student",
    response_model=APIResponse[StudentResponse],
    summary="提交学生信息（Level 2：需审核）",
)
async def submit_student(
    body: StudentSubmit,
    current_user=Depends(get_current_user),
):
    result = await UserService().submit_student(current_user.id, body)
    return success(data=result)


@router.get("/student",
    response_model=APIResponse[StudentResponse],
    summary="查看学生信息",
)
async def get_student(current_user=Depends(get_current_user)):
    result = await UserService().get_student(current_user.id)
    return success(data=result)


# ═══════ Level 2: 企业信息 ═══════

@router.post("/enterprise",
    response_model=APIResponse[EnterpriseResponse],
    summary="提交企业信息（Level 2：需审核）",
)
async def submit_enterprise(
    body: EnterpriseSubmit,
    current_user=Depends(get_current_user),
):
    result = await UserService().submit_enterprise(current_user.id, body)
    return success(data=result)


@router.get("/enterprise",
    response_model=APIResponse[EnterpriseResponse],
    summary="查看企业信息",
)
async def get_enterprise(current_user=Depends(get_current_user)):
    result = await UserService().get_enterprise(current_user.id)
    return success(data=result)
