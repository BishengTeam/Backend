"""H3C 认证报名 API"""
from fastapi import APIRouter, Depends

from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.schemas.common import APIResponse, success
from app.schemas.h3c import H3cOrderCreate, H3cOrderResponse, H3cProfileDefaults
from app.services.h3c_order import H3cOrderService

router = APIRouter(prefix="/orders/h3c", tags=["H3C 报名"])


@router.get("/profile",
    response_model=APIResponse[H3cProfileDefaults],
    summary="获取 H3C 报名预填值",
    description="""
从用户个人资料中提取 H3C 报名表单的预填默认值。

**使用场景**: 进入 H3C 报名页面时调用，前端用返回值预填表单。
用户可修改任意字段后提交。
    """,
)
async def get_h3c_profile_defaults(
    current_user: User = Depends(get_current_user),
) -> APIResponse[H3cProfileDefaults]:
    result = await H3cOrderService().get_profile_defaults(current_user.id)
    return success(data=result)


@router.post("",
    response_model=APIResponse[H3cOrderResponse],
    summary="H3C 认证报名",
    description="""
H3C 认证考试报名，创建订单。

**使用场景**: 用户填写完 H3C 报名表单后提交。

**请求体**: H3C 报名信息（含批次 ID、考生信息、考试信息、附件）

**认证**: 需登录
    """,
)
async def create_h3c_order(
    body: H3cOrderCreate,
    current_user: User = Depends(get_current_user),
) -> APIResponse[H3cOrderResponse]:
    result = await H3cOrderService().create_order(current_user.id, body)
    return success(data=result)
