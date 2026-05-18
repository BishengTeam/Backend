from datetime import datetime, timezone

from sqlalchemy import select

from app.core.database import get_db_ctx
from app.core.exceptions import BusinessException, NotFoundException, ThirdPartyException
from app.integrations.wechat_pay import WechatPayClient
from app.models.order import Order
from app.models.user import User
from app.schemas.payment import (
    PaymentCallbackRequest,
    PaymentCallbackResponse,
    PaymentPrepayRequest,
    PaymentPrepayResponse,
)
from app.services.order import apply_order_status_transition


class PaymentService:
    def __init__(self) -> None:
        self.wechat_pay = WechatPayClient()

    async def create_prepay(
        self, user_id: int, data: PaymentPrepayRequest
    ) -> PaymentPrepayResponse:
        async with get_db_ctx() as db:
            order = await db.get(Order, data.order_id)
            if order is None or order.user_id != user_id:
                raise NotFoundException("订单")
            if order.status != "pending":
                raise BusinessException("订单状态不允许发起支付")
            user = await db.get(User, user_id)
            if user is None or not user.is_active:
                raise NotFoundException("用户")
            if not order.out_trade_no:
                order.out_trade_no = str(order.id)
                await db.commit()
                await db.refresh(order)

            prepay = await self.wechat_pay.create_jsapi_prepay(
                openid=user.openid,
                out_trade_no=order.out_trade_no,
                body=f"{order.cert_type} 认证报名服务费",
                total_fee=order.price,
            )
            return PaymentPrepayResponse(
                order_id=order.id,
                out_trade_no=order.out_trade_no,
                **prepay,
            )

    async def handle_callback(self, data: PaymentCallbackRequest) -> PaymentCallbackResponse:
        payload = data.model_dump(mode="json", exclude_none=True)
        if not self.wechat_pay.verify_signature(payload):
            raise ThirdPartyException("微信支付回调签名验证失败")

        async with get_db_ctx() as db:
            order = (
                await db.execute(
                    select(Order)
                    .where(Order.out_trade_no == data.out_trade_no)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if order is None:
                raise NotFoundException("订单")

            processed = False
            metadata_changed = False
            if data.trade_state == "SUCCESS":
                if data.total_fee is not None and data.total_fee != order.price:
                    raise BusinessException("支付金额与订单金额不一致")
                if order.status == "pending":
                    processed = apply_order_status_transition(order, "paid")
                elif order.status not in {"paid", "completed"}:
                    apply_order_status_transition(order, "paid")
                if data.transaction_id and not order.transaction_id:
                    order.transaction_id = data.transaction_id
                    metadata_changed = True
                if not order.paid_at:
                    order.paid_at = data.paid_at or datetime.now(timezone.utc)
                    metadata_changed = True
            elif data.trade_state == "REFUND":
                if order.status != "refunded":
                    processed = apply_order_status_transition(order, "refunded")

            if processed or metadata_changed:
                await db.commit()
                await db.refresh(order)

            return PaymentCallbackResponse(
                order_id=order.id,
                status=order.status,
                processed=processed,
            )
