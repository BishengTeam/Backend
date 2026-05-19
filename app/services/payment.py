from datetime import datetime, timezone

from sqlalchemy import select

from app.core.database import get_db_ctx
from app.core.exceptions import BusinessException, ConflictException, NotFoundException, ThirdPartyException
from app.integrations.wechat_pay import WechatPayClient
from app.models.order import Order
from app.models.user import User
from app.schemas.payment import (
    PaymentCallbackRequest,
    PaymentCallbackResponse,
    PaymentPrepayRequest,
    PaymentPrepayResponse,
)
from app.services.inventory import confirm_inventory_sale, release_inventory_lock
from app.services.order import apply_order_status_transition

PREPAY_EXPIRATION_GUARD_SECONDS = 60


class PaymentService:
    def __init__(self) -> None:
        self.wechat_pay = WechatPayClient()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _is_expired(order: Order, now: datetime) -> bool:
        expires_at = PaymentService._normalized_expires_at(order)
        return expires_at is not None and expires_at <= now

    @staticmethod
    def _normalized_expires_at(order: Order) -> datetime | None:
        if order.expires_at is None:
            return None
        expires_at = order.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at

    @staticmethod
    def _seconds_until_expiration(order: Order, now: datetime) -> float | None:
        expires_at = PaymentService._normalized_expires_at(order)
        if expires_at is None:
            return None
        return (expires_at - now).total_seconds()

    @staticmethod
    def _is_expiring_soon(order: Order, now: datetime) -> bool:
        remaining_seconds = PaymentService._seconds_until_expiration(order, now)
        return (
            remaining_seconds is not None
            and remaining_seconds <= PREPAY_EXPIRATION_GUARD_SECONDS
        )

    async def _release_inventory_lock(self, db, order: Order) -> None:
        await release_inventory_lock(db, order, reason=order.close_reason or "expired")

    async def _confirm_inventory_sale(self, db, order: Order) -> None:
        await confirm_inventory_sale(db, order, reason="payment_success")

    async def _close_expired_order(self, db, order: Order, now: datetime) -> None:
        apply_order_status_transition(order, "closed")
        order.closed_at = now
        order.close_reason = "expired"
        await self._release_inventory_lock(db, order)

    async def _ensure_order_payable_for_prepay(self, db, order: Order, now: datetime) -> None:
        if order.status != "pending":
            raise BusinessException("订单状态不允许发起支付")
        if self._is_expired(order, now):
            await self._close_expired_order(db, order, now)
            await db.commit()
            await db.refresh(order)
            raise BusinessException("订单已过期，已关闭")
        if self._is_expiring_soon(order, now):
            # Avoid returning a prepay_id that the timeout worker can close immediately.
            raise BusinessException("订单即将过期，请重新下单")

    async def create_prepay(
        self, user_id: int, data: PaymentPrepayRequest
    ) -> PaymentPrepayResponse:
        async with get_db_ctx() as db:
            order = (
                await db.execute(
                    select(Order)
                    .where(Order.id == data.order_id, Order.user_id == user_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if order is None:
                raise NotFoundException("订单")
            now = self._now()
            await self._ensure_order_payable_for_prepay(db, order, now)
            user = await db.get(User, user_id)
            if user is None or not user.is_active:
                raise NotFoundException("用户")
            if not order.out_trade_no:
                order.out_trade_no = str(order.id)
            prepay_order_id = order.id
            prepay_out_trade_no = order.out_trade_no
            prepay_body = f"{order.cert_type} 认证报名服务费"
            prepay_total_fee = order.price
            user_openid = user.openid
            await db.commit()

            prepay = await self.wechat_pay.create_jsapi_prepay(
                openid=user_openid,
                out_trade_no=prepay_out_trade_no,
                body=prepay_body,
                total_fee=prepay_total_fee,
            )

        async with get_db_ctx() as db:
            order = (
                await db.execute(
                    select(Order)
                    .where(Order.id == prepay_order_id, Order.user_id == user_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if order is None:
                raise NotFoundException("订单")
            now = self._now()
            await self._ensure_order_payable_for_prepay(db, order, now)
            await db.commit()

            return PaymentPrepayResponse(
                order_id=prepay_order_id,
                out_trade_no=prepay_out_trade_no,
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
                if not data.transaction_id:
                    raise BusinessException("支付成功回调缺少微信交易号")
                if order.transaction_id and order.transaction_id != data.transaction_id:
                    raise ConflictException("微信交易号与订单记录不一致")
                duplicate_order = (
                    await db.execute(
                        select(Order)
                        .where(
                            Order.transaction_id == data.transaction_id,
                            Order.id != order.id,
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if duplicate_order is not None:
                    raise ConflictException("微信交易号已绑定其他订单")
                if order.status == "pending":
                    order.transaction_id = data.transaction_id
                    order.paid_at = data.paid_at or self._now()
                    processed = apply_order_status_transition(order, "paid")
                    await self._confirm_inventory_sale(db, order)
                elif order.status in {"paid", "completed"}:
                    if not order.transaction_id:
                        order.transaction_id = data.transaction_id
                        metadata_changed = True
                    if not order.paid_at:
                        order.paid_at = data.paid_at or self._now()
                        metadata_changed = True
                else:
                    raise ConflictException("订单状态不允许确认支付")
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
