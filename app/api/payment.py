from fastapi import APIRouter, Depends

from app.middleware.auth import get_current_user
from app.models.user import User
from app.schemas.common import APIResponse, success
from app.schemas.payment import (
    PaymentCallbackRequest,
    PaymentCallbackResponse,
    PaymentPrepayRequest,
    PaymentPrepayResponse,
)
from app.services.payment import PaymentService

router = APIRouter(prefix="/payment", tags=["支付"])


@router.post("/prepay", response_model=APIResponse[PaymentPrepayResponse])
async def prepay(
    body: PaymentPrepayRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[PaymentPrepayResponse]:
    """微信支付统一下单"""
    result = await PaymentService().create_prepay(current_user.id, body)
    return success(data=result)


@router.post("/callback", response_model=APIResponse[PaymentCallbackResponse])
async def payment_callback(body: PaymentCallbackRequest) -> APIResponse[PaymentCallbackResponse]:
    """支付回调通知"""
    result = await PaymentService().handle_callback(body)
    return success(data=result)
