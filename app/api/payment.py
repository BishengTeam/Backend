"""Retired WeChat Pay routes.

The product now settles every order through bank transfer, so WeChat Pay is
retired.  The four historical routes stay registered only to publish a stable
``410 Gone`` contract (and ``deprecated: true`` in OpenAPI); the
``RetiredPaymentRouteMiddleware`` already rejects them before authentication,
so these handlers are a second, defensive fence.
"""

from fastapi import APIRouter

from app.contracts.payment import (
    WECHAT_PAY_RETIRED_CODE,
    WECHAT_PAY_RETIRED_MESSAGE,
)
from app.port.exceptions import RetiredFeatureException

router = APIRouter(prefix="/payment", tags=["订单（微信支付已停用）"])

_RETIRED_RESPONSES = {
    410: {"description": f"微信支付已停用（{WECHAT_PAY_RETIRED_CODE}）"},
}


def _retired() -> None:
    raise RetiredFeatureException(
        WECHAT_PAY_RETIRED_MESSAGE,
        code=WECHAT_PAY_RETIRED_CODE,
    )


@router.post(
    "/prepay",
    deprecated=True,
    summary="[已停用] 微信支付 V3 JSAPI 预下单",
    description="微信支付已停用，请在订单中查看银行转账信息并提交转账凭证。",
    responses=_RETIRED_RESPONSES,
)
async def prepay() -> None:
    _retired()


@router.post(
    "/orders/{order_id}/sync",
    deprecated=True,
    summary="[已停用] 主动同步微信支付结果",
    description="微信支付已停用；转账核对以管理员核账记录为准。",
    responses=_RETIRED_RESPONSES,
)
async def sync_order(order_id: int) -> None:
    _retired()


@router.post(
    "/callback",
    deprecated=True,
    summary="[已停用] 微信支付 V3 支付结果通知",
    description="微信支付已停用，不再接受支付通知。",
    responses=_RETIRED_RESPONSES,
)
async def payment_callback() -> None:
    _retired()


@router.post(
    "/refund-callback",
    deprecated=True,
    summary="[已停用] 微信支付 V3 退款结果通知",
    description="微信支付已停用，退款以线下退款登记为准。",
    responses=_RETIRED_RESPONSES,
)
async def refund_callback() -> None:
    _retired()
