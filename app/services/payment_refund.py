"""Automatic full refunds for late WeChat payments on closed orders."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapter.database import get_db_ctx
from app.domain.order.src.index import Order, PaymentRefundTask
from app.integrations.wechat_pay import (
    WECHAT_PAY_CURRENCY,
    WechatPayAPIError,
    WechatPayClient,
    WechatPayRefund,
    WechatPayResultUnknownError,
)
from app.port.config import settings
from app.port.exceptions import ConflictException, NotFoundException

logger = logging.getLogger(__name__)

EXECUTABLE_STATUSES = ("queued", "processing", "failed")


@dataclass(frozen=True, slots=True)
class _TaskPayload:
    task_id: int
    out_trade_no: str
    out_refund_no: str
    transaction_id: str
    amount_cents: int
    retry_count: int
    submit_attempts: int


class PaymentRefundService:
    def __init__(self, wechat_pay: WechatPayClient | None = None) -> None:
        self.wechat_pay = wechat_pay or WechatPayClient()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _retry_delay(retry_count: int) -> int:
        delays = [
            int(value)
            for value in settings.WECHAT_PAY_REFUND_RETRY_DELAYS_SECONDS.split(",")
            if value.strip()
        ]
        return delays[min(retry_count, len(delays) - 1)]

    @classmethod
    async def schedule_late_payment_refund(
        cls, db: AsyncSession, order: Order
    ) -> PaymentRefundTask | None:
        """Create an idempotent refund task inside the payment transaction."""
        if order.status != "closed" or order.price <= 0:
            return None
        if not order.out_trade_no or not order.transaction_id:
            raise ConflictException("迟到支付订单缺少微信支付凭证")

        existing = (
            await db.execute(
                select(PaymentRefundTask)
                .where(
                    PaymentRefundTask.order_id == order.id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        task = PaymentRefundTask(
            order_id=order.id,
            user_id=order.user_id,
            amount_cents=order.price,
            out_trade_no=order.out_trade_no,
            transaction_id=order.transaction_id,
            status="queued",
            reason="late_payment_refund",
            retry_count=0,
            submit_attempts=0,
            next_attempt_at=cls._now(),
            # One closed order has at most one full late-payment refund. Using
            # the stable order id avoids a transient NULL merchant refund number
            # while the task row is being flushed.
            out_refund_no=f"LPRF{order.id:024d}",
        )
        db.add(task)
        await db.flush()
        return task

    async def process(self, task_id: int) -> PaymentRefundTask:
        payload = await self._mark_processing(task_id)
        if payload is None:
            async with get_db_ctx() as db:
                return await self._task(db, task_id)

        if payload.retry_count >= settings.WECHAT_PAY_LATE_REFUND_MAX_QUERY_ATTEMPTS:
            return await self._mark_manual(
                task_id, reason="late refund query attempts exhausted"
            )

        provider_refund = await self._query_or_submit(payload)
        return await self._apply_provider_result(task_id, provider_refund)

    async def handle_callback_raw(
        self, *, raw_body: bytes, headers: dict[str, str]
    ) -> PaymentRefundTask:
        provider = self.wechat_pay.parse_refund_notification(
            headers=headers,
            raw_body=raw_body,
        )
        async with get_db_ctx() as db:
            task = await db.scalar(
                select(PaymentRefundTask)
                .where(PaymentRefundTask.out_refund_no == provider.out_refund_no)
                .limit(1)
            )
            if task is None:
                raise NotFoundException("迟到支付退款任务")
            task_id = task.id
        return await self._apply_provider_result(task_id, provider)

    async def _mark_processing(self, task_id: int) -> _TaskPayload | None:
        async with get_db_ctx() as db:
            unresolved = await self._task(db, task_id)
            order = await db.scalar(
                select(Order)
                .where(Order.id == unresolved.order_id)
                .with_for_update()
            )
            if order is None:
                raise NotFoundException("迟到支付退款订单")
            task = await self._task_for_update(db, task_id)
            if task.status == "succeeded":
                return None
            if task.status not in EXECUTABLE_STATUSES:
                raise ConflictException("迟到支付退款任务当前不可执行")
            if task.next_attempt_at > self._now():
                # Another worker owns the attempt lease.
                return None
            task.status = "processing"
            task.processing_at = task.processing_at or self._now()
            task.next_attempt_at = self._now() + timedelta(
                seconds=self._retry_delay(task.retry_count)
            )
            await db.commit()
            return _TaskPayload(
                task_id=task.id,
                out_trade_no=task.out_trade_no,
                out_refund_no=task.out_refund_no,
                transaction_id=task.transaction_id,
                amount_cents=task.amount_cents,
                retry_count=task.retry_count,
                submit_attempts=task.submit_attempts,
            )

    async def _query_or_submit(self, payload: _TaskPayload) -> WechatPayRefund:
        try:
            raw = await self.wechat_pay.query_refund(
                out_refund_no=payload.out_refund_no
            )
            return WechatPayRefund.from_payload(raw, require_mchid=True)
        except WechatPayAPIError as exc:
            provider_missing = (
                exc.status_code == 404
                or exc.api_code
                in {"NOT_FOUND", "RESOURCE_NOT_EXISTS", "REFUND_NOT_EXISTS"}
            )
            if not provider_missing:
                await self._record_attempt(
                    payload.task_id,
                    error=f"{type(exc).__name__}:{exc.api_code}",
                    result_unknown=exc.status_code >= 500,
                )
                raise
            if (
                payload.submit_attempts
                >= settings.WECHAT_PAY_LATE_REFUND_MAX_SUBMIT_ATTEMPTS
            ):
                # A 404 after prior submits is still inconclusive because query
                # propagation can lag. Continue querying instead of inventing a
                # new merchant refund number.
                await self._record_attempt(
                    payload.task_id,
                    error="refund_not_found_before_submit_limit",
                    result_unknown=True,
                )
                raise ConflictException("微信退款单暂未查询到，继续对账") from exc

        if not await self._record_submit_attempt(payload.task_id):
            raise ConflictException("迟到支付退款提交次数已达上限")
        try:
            raw = await self.wechat_pay.refund(
                out_trade_no=payload.out_trade_no,
                out_refund_no=payload.out_refund_no,
                amount_total=payload.amount_cents,
                refund_amount=payload.amount_cents,
                reason="迟到支付自动退款",
                notify_url=settings.WECHAT_PAY_REFUND_NOTIFY_URL,
            )
        except (WechatPayResultUnknownError, WechatPayAPIError) as exc:
            await self._record_attempt(
                payload.task_id,
                error=f"{type(exc).__name__}:{exc}",
                result_unknown=isinstance(exc, WechatPayResultUnknownError)
                or (
                    isinstance(exc, WechatPayAPIError) and exc.status_code >= 500
                ),
            )
            raise
        return WechatPayRefund.from_payload(raw, require_mchid=True)

    async def _record_submit_attempt(self, task_id: int) -> bool:
        """Persist the idempotent submit attempt before crossing the network."""
        async with get_db_ctx() as db:
            unresolved = await self._task(db, task_id)
            order = await db.scalar(
                select(Order)
                .where(Order.id == unresolved.order_id)
                .with_for_update()
            )
            if order is None:
                raise NotFoundException("迟到支付退款订单")
            task = await self._task_for_update(db, task_id)
            if task.status == "succeeded":
                return False
            if task.status not in EXECUTABLE_STATUSES:
                raise ConflictException("迟到支付退款任务当前不可执行")
            if (
                task.submit_attempts
                >= settings.WECHAT_PAY_LATE_REFUND_MAX_SUBMIT_ATTEMPTS
            ):
                return False
            task.submit_attempts += 1
            task.status = "processing"
            task.processing_at = task.processing_at or self._now()
            task.next_attempt_at = self._now() + timedelta(
                seconds=self._retry_delay(task.retry_count)
            )
            await db.commit()
            return True

    async def _apply_provider_result(
        self, task_id: int, provider: WechatPayRefund
    ) -> PaymentRefundTask:
        async with get_db_ctx() as db:
            unresolved = await self._task(db, task_id)
            order = await db.scalar(
                select(Order).where(Order.id == unresolved.order_id).with_for_update()
            )
            if order is None:
                raise NotFoundException("迟到支付退款订单")
            task = await self._task_for_update(db, task_id)
            self._validate(task, order, provider)
            if task.status == "succeeded":
                return task
            task.last_provider_status = provider.status
            task.wechat_refund_id = provider.refund_id
            task.last_error = None
            task.retry_count += 1
            if provider.status == "SUCCESS":
                task.status = "succeeded"
                task.succeeded_at = provider.success_time or self._now()
            elif provider.status == "PROCESSING":
                task.status = "processing"
                task.next_attempt_at = self._now() + timedelta(
                    seconds=self._retry_delay(task.retry_count)
                )
            elif provider.status in {"ABNORMAL", "CLOSED"}:
                task.status = "manual_review"
                task.manual_takeover_at = self._now()
                task.last_error = f"WechatRefundStatus:{provider.status}"
            else:
                task.status = "failed"
                task.next_attempt_at = self._now() + timedelta(
                    seconds=self._retry_delay(task.retry_count)
                )
            await db.commit()
            await db.refresh(task)
            return task

    @staticmethod
    def _validate(
        task: PaymentRefundTask, order: Order, provider: WechatPayRefund
    ) -> None:
        if (
            provider.out_trade_no != task.out_trade_no
            or provider.out_trade_no != order.out_trade_no
            or provider.out_refund_no != task.out_refund_no
            or provider.transaction_id != task.transaction_id
            or provider.transaction_id != order.transaction_id
            or provider.amount_total != task.amount_cents
            or provider.amount_refund != task.amount_cents
            or provider.currency != WECHAT_PAY_CURRENCY
        ):
            raise ConflictException("微信迟到支付退款结果与任务不一致")
        if provider.mchid and provider.mchid != settings.WECHAT_PAY_MCHID:
            raise ConflictException("微信迟到支付退款商户号不一致")

    async def _record_attempt(
        self, task_id: int, *, error: str, result_unknown: bool
    ) -> None:
        async with get_db_ctx() as db:
            unresolved = await self._task(db, task_id)
            order = await db.scalar(
                select(Order)
                .where(Order.id == unresolved.order_id)
                .with_for_update()
            )
            if order is None:
                raise NotFoundException("迟到支付退款订单")
            task = await self._task_for_update(db, task_id)
            if task.status == "succeeded":
                return
            task.retry_count += 1
            task.last_error = error[:2000]
            task.status = "processing" if result_unknown else "failed"
            task.next_attempt_at = self._now() + timedelta(
                seconds=self._retry_delay(task.retry_count)
            )
            if task.retry_count >= settings.WECHAT_PAY_LATE_REFUND_MAX_QUERY_ATTEMPTS:
                task.status = "manual_review"
                task.manual_takeover_at = self._now()
            await db.commit()

    async def _mark_manual(self, task_id: int, *, reason: str) -> PaymentRefundTask:
        async with get_db_ctx() as db:
            unresolved = await self._task(db, task_id)
            order = await db.scalar(
                select(Order)
                .where(Order.id == unresolved.order_id)
                .with_for_update()
            )
            if order is None:
                raise NotFoundException("迟到支付退款订单")
            task = await self._task_for_update(db, task_id)
            if task.status != "succeeded":
                task.status = "manual_review"
                task.manual_takeover_at = self._now()
                task.last_error = reason
            await db.commit()
            await db.refresh(task)
            return task

    @staticmethod
    async def _task(db: AsyncSession, task_id: int) -> PaymentRefundTask:
        task = await db.get(PaymentRefundTask, task_id)
        if task is None:
            raise NotFoundException("迟到支付退款任务")
        return task

    @staticmethod
    async def _task_for_update(db: AsyncSession, task_id: int) -> PaymentRefundTask:
        task = (
            await db.execute(
                select(PaymentRefundTask)
                .where(PaymentRefundTask.id == task_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if task is None:
            raise NotFoundException("迟到支付退款任务")
        return task


async def late_payment_refund_worker_loop() -> None:
    service = PaymentRefundService()
    last_task_id = 0
    while True:
        try:
            async with get_db_ctx() as db:
                rows = (
                    await db.execute(
                        select(PaymentRefundTask)
                        .where(
                            PaymentRefundTask.status.in_(EXECUTABLE_STATUSES),
                            PaymentRefundTask.next_attempt_at <= service._now(),
                            PaymentRefundTask.id > last_task_id,
                        )
                        .order_by(PaymentRefundTask.id)
                        .limit(settings.WECHAT_PAY_REFUND_RECONCILE_BATCH_SIZE)
                    )
                ).scalars().all()
                task_ids = [row.id for row in rows]
                last_task_id = rows[-1].id if rows else 0
            for task_id in task_ids:
                try:
                    await service.process(task_id)
                except Exception as exc:
                    logger.warning(
                        "late payment refund task failed: id=%s type=%s",
                        task_id,
                        type(exc).__name__,
                    )
        except Exception as exc:
            last_task_id = 0
            logger.error(
                "late payment refund scan failed: type=%s", type(exc).__name__
            )
        await asyncio.sleep(settings.WECHAT_PAY_REFUND_RECONCILE_POLL_SECONDS)
