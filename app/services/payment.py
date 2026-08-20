"""Payment orchestration shared by V3 notification, active sync, and worker."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.adapter.database import get_db_ctx
from app.domain.certification.src.index import Course, CourseEnrollment
from app.domain.h3c.src.index import H3cRefundRequest
from app.domain.order.src.index import (
    Order,
    apply_order_status_transition,
    confirm_inventory_sale,
    refund_inventory_sale,
    release_inventory_lock,
)
from app.domain.user.src.index import User
from app.integrations.wechat_pay import (
    WECHAT_PAY_CURRENCY,
    WechatPayAPIError,
    WechatPayClient,
    WechatPayTransaction,
)
from app.port.exceptions import (
    BusinessException,
    ConflictException,
    NotFoundException,
    ThirdPartyException,
)
from app.schemas.payment import (
    PaymentCallbackResponse,
    PaymentPrepayRequest,
    PaymentPrepayResponse,
    PaymentSyncResponse,
)
from app.services.order_fulfillment import OrderFulfillmentService
from app.utils.payment import generate_out_trade_no

logger = logging.getLogger(__name__)

PREPAY_EXPIRATION_GUARD_SECONDS = 60
SUPPORTED_TRANSACTION_STATES = {
    "SUCCESS",
    "REFUND",
    "NOTPAY",
    "CLOSED",
    "REVOKED",
    "USERPAYING",
    "PAYERROR",
}


class PaymentService:
    def __init__(self) -> None:
        self.wechat_pay = WechatPayClient()
        self.fulfillment = OrderFulfillmentService()

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

    async def _refund_inventory_sale(self, db, order: Order) -> bool:
        return await refund_inventory_sale(
            db,
            order,
            reason="payment_refund_reconciliation",
        )

    async def _close_expired_order(self, db, order: Order, now: datetime) -> None:
        apply_order_status_transition(order, "closed")
        order.closed_at = now
        order.close_reason = "expired"
        await self._release_inventory_lock(db, order)
        await self.fulfillment.on_closed(db, order)

    async def _ensure_order_payable_for_prepay(
        self, db, order: Order, now: datetime
    ) -> None:
        if order.status != "pending":
            raise BusinessException("订单状态不允许发起支付")
        if self._is_expired(order, now):
            await self._close_expired_order(db, order, now)
            await db.commit()
            await db.refresh(order)
            raise BusinessException("订单已过期，已关闭")
        if self._is_expiring_soon(order, now):
            raise BusinessException("订单即将过期，请重新下单")
        if order.order_kind == "course":
            enrollment = (
                await db.execute(
                    select(CourseEnrollment).where(
                        CourseEnrollment.order_id == order.id
                    )
                )
            ).scalar_one_or_none()
            course = (
                await db.get(Course, enrollment.course_id)
                if enrollment is not None
                else None
            )
            if enrollment is None or course is None or course.status != "published":
                if order.status == "pending":
                    apply_order_status_transition(order, "closed")
                    order.closed_at = now
                    order.close_reason = "course_not_purchasable"
                    await self.fulfillment.on_closed(db, order)
                    await db.commit()
                    await db.refresh(order)
                raise BusinessException("课程已下线，订单无法支付")

    async def create_prepay(
        self, user_id: int, data: PaymentPrepayRequest
    ) -> PaymentPrepayResponse:
        """Create/retry one remote JSAPI order for the existing business order."""

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
            if not user.openid:
                raise BusinessException("当前账号缺少微信身份，无法支付")
            # Retrying prepay must keep the same merchant order number.  The
            # WeChat V3 endpoint therefore remains idempotent even if the
            # caller did not receive the first response.
            if not order.out_trade_no:
                order.out_trade_no = generate_out_trade_no("ORD")
            prepay_order_id = order.id
            prepay_out_trade_no = order.out_trade_no
            prepay_description = f"{order.product_type} 订单服务费"
            prepay_amount_total = order.price
            prepay_expiration = self._normalized_expires_at(order)
            prepay_attach = f"order:{order.id}"
            user_openid = user.openid
            await db.commit()

        prepay = await self.wechat_pay.create_jsapi_prepay(
            openid=user_openid,
            out_trade_no=prepay_out_trade_no,
            description=prepay_description,
            amount_total=prepay_amount_total,
            attach=prepay_attach,
            time_expire=prepay_expiration,
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
            if (
                order.out_trade_no != prepay_out_trade_no
                or order.price != prepay_amount_total
            ):
                raise ConflictException("预下单期间订单关键信息已变化")
            await db.commit()

        return PaymentPrepayResponse(
            order_id=prepay_order_id,
            out_trade_no=prepay_out_trade_no,
            **prepay,
        )

    async def handle_callback_raw(
        self, *, raw_body: bytes, headers: dict[str, str]
    ) -> PaymentCallbackResponse:
        transaction = self.wechat_pay.parse_payment_notification(
            headers=headers,
            raw_body=raw_body,
        )
        return await self._apply_transaction(
            transaction,
            source="notification",
            verify_provider_fields=True,
        )

    async def sync_order(self, user_id: int, order_id: int) -> PaymentSyncResponse:
        """Query WeChat for one user-owned order and atomically reconcile it."""

        async with get_db_ctx() as db:
            order = (
                await db.execute(
                    select(Order).where(
                        Order.id == order_id,
                        Order.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            if order is None:
                raise NotFoundException("订单")
            if not order.out_trade_no:
                raise BusinessException("订单尚未生成商户订单号")
            out_trade_no = order.out_trade_no

        try:
            transaction = await self.wechat_pay.query_order(
                out_trade_no=out_trade_no
            )
        except WechatPayAPIError as exc:
            if exc.api_code != "ORDER_NOT_EXIST":
                raise
            return await self._local_sync_response(
                order_id=order_id,
                user_id=user_id,
                trade_state="NOTPAY",
            )
        result = await self._apply_transaction(
            transaction,
            source="user_sync",
            expected_order_id=order_id,
            expected_user_id=user_id,
            verify_provider_fields=True,
        )
        return PaymentSyncResponse(
            **result.model_dump(),
            trade_state=transaction.trade_state,
            synchronized_at=self._now(),
        )

    async def sync_pending_order(self, order_id: int) -> PaymentSyncResponse | None:
        """Worker entry point; a concurrent state change is an idempotent no-op."""

        async with get_db_ctx() as db:
            order = await db.get(Order, order_id)
            if order is None or order.status != "pending" or not order.out_trade_no:
                return None
            out_trade_no = order.out_trade_no

        try:
            transaction = await self.wechat_pay.query_order(
                out_trade_no=out_trade_no
            )
        except WechatPayAPIError as exc:
            if exc.api_code == "ORDER_NOT_EXIST":
                return None
            raise
        result = await self._apply_transaction(
            transaction,
            source="reconciliation_worker",
            expected_order_id=order_id,
            verify_provider_fields=True,
        )
        return PaymentSyncResponse(
            **result.model_dump(),
            trade_state=transaction.trade_state,
            synchronized_at=self._now(),
        )

    async def _local_sync_response(
        self, *, order_id: int, user_id: int, trade_state: str
    ) -> PaymentSyncResponse:
        async with get_db_ctx() as db:
            order = (
                await db.execute(
                    select(Order).where(
                        Order.id == order_id,
                        Order.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            if order is None:
                raise NotFoundException("订单")
            return PaymentSyncResponse(
                order_id=order.id,
                status=order.status,
                processed=False,
                trade_state=trade_state,
                synchronized_at=self._now(),
            )

    async def _validate_provider_transaction(
        self, db, order: Order, transaction: WechatPayTransaction
    ) -> None:
        if transaction.appid != self.wechat_pay.appid:
            raise BusinessException("微信支付 AppID 与订单配置不一致")
        if transaction.mchid != self.wechat_pay.mch_id:
            raise BusinessException("微信支付商户号与订单配置不一致")
        if transaction.amount_total != order.price:
            raise BusinessException("支付金额与订单金额不一致")
        if transaction.currency != WECHAT_PAY_CURRENCY:
            raise BusinessException("微信支付币种与订单不一致")
        if transaction.attach != f"order:{order.id}":
            raise BusinessException("微信支付 attach 与订单不一致")
        if transaction.trade_state == "SUCCESS":
            user = await db.get(User, order.user_id)
            if user is None or transaction.payer_openid != user.openid:
                raise BusinessException("微信支付付款人与订单用户不一致")

    @staticmethod
    def _record_late_payment(
        order: Order, transaction: WechatPayTransaction, reason: str
    ) -> None:
        extra = dict(order.extra_data or {})
        payment_metadata = dict(extra.get("_wechat_pay_v3") or {})
        payment_metadata["late_payment"] = {
            "transaction_id": transaction.transaction_id,
            "success_time": (
                transaction.success_time.isoformat()
                if transaction.success_time is not None
                else None
            ),
            "reason": reason,
            "requires_refund_review": True,
        }
        extra["_wechat_pay_v3"] = payment_metadata
        order.extra_data = extra

    async def _apply_transaction(
        self,
        transaction: WechatPayTransaction,
        *,
        source: str,
        expected_order_id: int | None = None,
        expected_user_id: int | None = None,
        verify_provider_fields: bool,
    ) -> PaymentCallbackResponse:
        """The single row-locked transaction used by all result sources."""

        if transaction.trade_state not in SUPPORTED_TRANSACTION_STATES:
            raise ThirdPartyException("微信支付返回了不支持的交易状态")
        filters = [Order.out_trade_no == transaction.out_trade_no]
        if expected_order_id is not None:
            filters.append(Order.id == expected_order_id)
        if expected_user_id is not None:
            filters.append(Order.user_id == expected_user_id)

        async with get_db_ctx() as db:
            order = (
                await db.execute(select(Order).where(*filters).with_for_update())
            ).scalar_one_or_none()
            if order is None:
                raise NotFoundException("订单")
            if verify_provider_fields:
                await self._validate_provider_transaction(db, order, transaction)

            processed = False
            metadata_changed = False
            if transaction.trade_state == "SUCCESS":
                if not transaction.transaction_id:
                    raise BusinessException("支付成功结果缺少微信交易号")
                if order.transaction_id and order.transaction_id != transaction.transaction_id:
                    raise ConflictException("微信交易号与订单记录不一致")
                duplicate_order = (
                    await db.execute(
                        select(Order)
                        .where(
                            Order.transaction_id == transaction.transaction_id,
                            Order.id != order.id,
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if duplicate_order is not None:
                    raise ConflictException("微信交易号已绑定其他订单")

                expires_at = self._normalized_expires_at(order)
                paid_after_expiration = bool(
                    expires_at is not None
                    and transaction.success_time is not None
                    and transaction.success_time > expires_at
                )
                if order.status == "pending" and paid_after_expiration:
                    processed = apply_order_status_transition(order, "closed")
                    order.closed_at = self._now()
                    order.close_reason = "payment_after_expiration"
                    await self._release_inventory_lock(db, order)
                    fulfillment_closed = await self.fulfillment.on_closed(db, order)
                    processed = processed or fulfillment_closed
                    order.transaction_id = transaction.transaction_id
                    order.paid_at = transaction.success_time
                    self._record_late_payment(
                        order, transaction, "provider_success_after_expiration"
                    )
                    metadata_changed = True
                elif order.status == "closed":
                    if not order.transaction_id:
                        order.transaction_id = transaction.transaction_id
                    if not order.paid_at:
                        order.paid_at = transaction.success_time or self._now()
                    self._record_late_payment(
                        order, transaction, "local_order_already_closed"
                    )
                    metadata_changed = True
                elif order.status == "pending":
                    order.transaction_id = transaction.transaction_id
                    order.paid_at = transaction.success_time or self._now()
                    target_status = (
                        "completed" if order.order_kind == "course" else "paid"
                    )
                    processed = apply_order_status_transition(order, target_status)
                    await self._confirm_inventory_sale(db, order)
                    fulfilled = await self.fulfillment.on_paid(db, order)
                    processed = processed or fulfilled
                elif order.status in {"paid", "completed"}:
                    if not order.transaction_id:
                        order.transaction_id = transaction.transaction_id
                        metadata_changed = True
                    if not order.paid_at:
                        order.paid_at = transaction.success_time or self._now()
                        metadata_changed = True
                    fulfilled = await self.fulfillment.on_paid(db, order)
                    processed = processed or fulfilled
                elif order.status == "refunded":
                    # A delayed success event must never roll a refunded order
                    # backwards.  Provider-field validation above still makes
                    # a forged/reused transaction fail closed.
                    metadata_changed = False
            elif transaction.trade_state == "REFUND":
                # Human-resources refunds have their own request/notification
                # state machine in stage four.  Transaction query must not
                # bypass it merely because WeChat reports REFUND here.
                has_h3c_refund = (
                    await db.scalar(
                        select(H3cRefundRequest.id)
                        .where(H3cRefundRequest.order_id == order.id)
                        .limit(1)
                    )
                ) is not None
                if order.application_id is None and not has_h3c_refund:
                    if order.status == "refunded":
                        processed = await self._refund_inventory_sale(db, order)
                    elif order.status in {"paid", "completed"}:
                        processed = apply_order_status_transition(order, "refunded")
                        inventory_refunded = await self._refund_inventory_sale(db, order)
                        processed = processed or inventory_refunded
                    if order.status == "refunded":
                        access_revoked = await self.fulfillment.on_refunded(db, order)
                        processed = processed or access_revoked
            elif transaction.trade_state in {"CLOSED", "REVOKED"}:
                if order.status == "pending":
                    processed = apply_order_status_transition(order, "closed")
                    order.closed_at = self._now()
                    order.close_reason = f"wechat_{transaction.trade_state.lower()}"
                    await self._release_inventory_lock(db, order)
                    fulfillment_closed = await self.fulfillment.on_closed(db, order)
                    processed = processed or fulfillment_closed
                elif order.status == "closed":
                    processed = await self.fulfillment.on_closed(db, order)
                # A delayed close result for a paid/refunded order is stale and
                # intentionally has no side effect.

            if processed or metadata_changed:
                try:
                    await db.commit()
                except IntegrityError as exc:
                    await db.rollback()
                    raise ConflictException("微信交易号已绑定其他订单") from exc
            logger.info(
                "payment transaction reconciled: order_id=%s source=%s trade_state=%s processed=%s",
                order.id,
                source,
                transaction.trade_state,
                processed,
            )
            return PaymentCallbackResponse(
                order_id=order.id,
                status=order.status,
                processed=processed,
            )


__all__ = ["PaymentService", "SUPPORTED_TRANSACTION_STATES"]
