from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.order import OrderStatus

PaymentTradeState = Literal[
    "SUCCESS",
    "REFUND",
    "NOTPAY",
    "CLOSED",
    "REVOKED",
    "USERPAYING",
    "PAYERROR",
]


class PaymentPrepayRequest(BaseModel):
    order_id: int = Field(..., gt=0, description="订单 ID")


class PaymentPrepayResponse(BaseModel):
    order_id: int = Field(..., description="订单 ID")
    out_trade_no: str = Field(..., description="商户订单号")
    prepay_id: str = Field(..., description="微信支付 prepay_id")
    time_stamp: str = Field(..., description="小程序支付 timeStamp")
    nonce_str: str = Field(..., description="小程序支付 nonceStr")
    package: str = Field(..., description="小程序支付 package")
    sign_type: str = Field(..., description="签名类型")
    pay_sign: str = Field(..., description="小程序支付 paySign")


class PaymentCallbackResponse(BaseModel):
    order_id: int = Field(..., description="订单 ID")
    status: OrderStatus = Field(..., description="订单状态")
    processed: bool = Field(..., description="本次回调是否导致状态变更")


class PaymentSyncResponse(PaymentCallbackResponse):
    trade_state: PaymentTradeState = Field(..., description="微信侧交易状态")
    synchronized_at: datetime = Field(..., description="本次查单完成时间")


class WechatPayNotificationAck(BaseModel):
    code: Literal["SUCCESS", "FAIL"]
    message: str
