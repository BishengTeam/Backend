from fastapi import APIRouter, Depends, Request

from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.schemas.common import APIResponse, success
from app.schemas.payment import (
    PaymentCallbackResponse,
    PaymentPrepayRequest,
    PaymentPrepayResponse,
)
from app.services.payment import PaymentService

router = APIRouter(prefix="/payment", tags=["支付"])


@router.post("/prepay",
    response_model=APIResponse[PaymentPrepayResponse],
    summary="支付预下单",
    description="""
小程序 **支付** 流程使用。

**使用场景**: 微信支付统一下单，返回 prepay_id 用于调起支付

**请求体**:
- `order_id`: 订单 ID

**响应**: prepay_id 等支付参数

**认证**: 需登录
    """,
)
async def prepay(
    body: PaymentPrepayRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[PaymentPrepayResponse]:
    """微信支付统一下单"""
    result = await PaymentService().create_prepay(current_user.id, body)
    return success(data=result)


@router.post("/callback",
    response_model=APIResponse[PaymentCallbackResponse],
    summary="支付回调",
    description="""
微信支付异步通知回调。

**使用场景**: 微信支付平台异步通知支付结果

**认证**: 无需登录（微信服务端调用）
    """,
)
async def payment_callback(request: Request) -> APIResponse[PaymentCallbackResponse]:
    """支付回调通知"""
    raw_body = await request.body()
    result = await PaymentService().handle_callback_raw(raw_body)
    return success(data=result)