from fastapi import APIRouter, Depends

from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.schemas.common import APIResponse, success
from app.schemas.user import (
    PhoneDecryptRequest,
    UserIdentityCreate,
    UserIdentityResponse,
    UserProfileDetail,
    UserProfileUpdate,
    UserUnbindRequest,
)
from app.services.user import UserService

router = APIRouter(prefix="/user", tags=["用户"])


@router.delete("/account",
    response_model=APIResponse,
    summary="注销账号",
    description="""
小程序 **个人中心** 页面使用。

**使用场景**: 用户在设置中选择注销账号，永久删除账号及关联数据

**关联页面**: 设置 → 账号安全 → 注销账号

**认证**: 需登录
    """,
)
async def delete_account(current_user: User = Depends(get_current_user)):
    """注销账号"""
    await UserService().delete_account(current_user.id)
    return success(message="账号已注销")


@router.post("/phone/decrypt",
    response_model=APIResponse,
    summary="解密手机号",
    description="""
小程序 **登录/绑定手机号** 页面使用。

**使用场景**: 用户授权微信手机号后，将加密数据解密为明文手机号并绑定到当前账号

**请求体**:
- `encrypted_data`: 微信加密数据
- `iv`: 加密初始向量

**认证**: 需登录
    """,
)
async def decrypt_phone(
    body: PhoneDecryptRequest,
    current_user: User = Depends(get_current_user),
):
    """解密微信手机号"""
    phone = await UserService().decrypt_phone(
        current_user.id, body.encrypted_data, body.iv
    )
    return success(data={"phone": phone})


@router.post("/identity",
    response_model=APIResponse[UserIdentityResponse],
    summary="提交实名认证",
    description="""
小程序 **实名认证** 页面使用。

**使用场景**: 用户提交姓名和身份证号进行实名认证，认证通过后方可参与竞赛报名等需要实名的操作

**关联页面**: 个人中心 → 实名认证

**认证**: 需登录
    """,
)
async def submit_identity(
    body: UserIdentityCreate,
    current_user: User = Depends(get_current_user),
) -> APIResponse[UserIdentityResponse]:
    """提交实名认证信息"""
    result = await UserService().submit_identity(current_user.id, body)
    return success(data=result)


@router.get("/identity",
    response_model=APIResponse[UserIdentityResponse],
    summary="查询实名认证状态",
    description="""
小程序 **实名认证** 页面使用。

**使用场景**: 页面加载时查询当前用户的实名认证状态和认证信息

**关联页面**: 个人中心 → 实名认证

**认证**: 需登录
    """,
)
async def get_identity(
    current_user: User = Depends(get_current_user),
) -> APIResponse[UserIdentityResponse]:
    """查询实名认证状态"""
    result = await UserService().get_identity(current_user.id)
    return success(data=result)


@router.get("/profile",
    response_model=APIResponse[UserProfileDetail],
    summary="获取个人信息",
    description="""
小程序 **个人中心** 页面使用。

**使用场景**: 页面加载时获取当前用户的个人资料信息

**关联页面**: 个人中心 → 个人信息

**认证**: 需登录
    """,
)
async def get_profile(
    current_user: User = Depends(get_current_user),
) -> APIResponse[UserProfileDetail]:
    """获取用户个人信息"""
    result = await UserService().get_profile(current_user.id)
    return success(data=result)


@router.put("/profile",
    response_model=APIResponse[UserProfileDetail],
    summary="编辑个人信息",
    description="""
小程序 **个人中心** 页面使用。

**使用场景**: 用户编辑个人信息，可重新绑定手机号

**关联页面**: 个人中心 → 个人信息 → 编辑

**认证**: 需登录
    """,
)
async def update_profile(
    body: UserProfileUpdate,
    current_user: User = Depends(get_current_user),
) -> APIResponse[UserProfileDetail]:
    """编辑个人信息（重新绑定手机号）"""
    result = await UserService().update_profile(current_user.id, body)
    return success(data=result)


@router.post("/unbind",
    response_model=APIResponse,
    summary="解绑账号",
    description="""
小程序 **账号安全** 页面使用。

**使用场景**: 用户解绑手机号或微信绑定

**请求体**: 解绑类型（phone / wechat）

**关联页面**: 个人中心 → 设置 → 账号安全

**认证**: 需登录
    """,
)
async def unbind(
    body: UserUnbindRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse:
    """解绑手机号/微信"""
    await UserService().unbind(current_user.id, body.type)
    return success(message="解绑成功")