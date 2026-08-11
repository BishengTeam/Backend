"""Bounded, restart-safe reconciliation of pending WeChat Pay orders."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.domain.order.src.index import Order
from app.port.config import settings
from app.services.payment import PaymentService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PaymentReconciliationBatch:
    scanned: int = 0
    synchronized: int = 0
    changed: int = 0
    failed: int = 0
    next_after_id: int = 0


@dataclass(slots=True)
class PaymentReconciliationMetrics:
    runs: int = 0
    scanned: int = 0
    synchronized: int = 0
    changed: int = 0
    failures: int = 0
    last_started_at: str | None = None
    last_finished_at: str | None = None
    last_runtime_seconds: float | None = None
    last_error_type: str | None = None
    queue_depth: int = 0

    def snapshot(self) -> dict[str, int | float | str | None]:
        return asdict(self)


payment_reconciliation_metrics = PaymentReconciliationMetrics()


class PaymentReconciliationService:
    def __init__(self, payment_service: PaymentService | None = None) -> None:
        self.payment_service = payment_service or PaymentService()

    async def pending_count(self) -> int:
        async with get_db_ctx() as db:
            return int(
                await db.scalar(
                    select(func.count())
                    .select_from(Order)
                    .where(
                        Order.status == "pending",
                        Order.out_trade_no.is_not(None),
                    )
                )
                or 0
            )

    async def reconcile_batch(
        self,
        *,
        after_id: int = 0,
        limit: int | None = None,
    ) -> PaymentReconciliationBatch:
        batch_limit = limit or settings.WECHAT_PAY_RECONCILE_BATCH_SIZE
        if batch_limit < 1 or batch_limit > 500:
            raise ValueError("payment reconciliation limit must be between 1 and 500")
        async with get_db_ctx() as db:
            order_ids = list(
                (
                    await db.scalars(
                        select(Order.id)
                        .where(
                            Order.status == "pending",
                            Order.out_trade_no.is_not(None),
                            Order.id > after_id,
                        )
                        .order_by(Order.id.asc())
                        .limit(batch_limit)
                    )
                ).all()
            )

        result = PaymentReconciliationBatch(scanned=len(order_ids))
        for order_id in order_ids:
            try:
                synced = await self.payment_service.sync_pending_order(order_id)
                if synced is not None:
                    result.synchronized += 1
                    if synced.processed:
                        result.changed += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # One malformed/provider-failed order must not starve the rest
                # of the batch.  No response body, identity data, or trade
                # number is written to logs.
                result.failed += 1
                logger.error(
                    "payment reconciliation item failed: order_id=%s exception_type=%s",
                    order_id,
                    type(exc).__name__,
                )
        result.next_after_id = order_ids[-1] if len(order_ids) == batch_limit else 0
        return result


async def payment_reconciliation_worker_loop(
    service: PaymentReconciliationService | None = None,
) -> None:
    active_service = service or PaymentReconciliationService()
    after_id = 0
    logger.info(
        "payment reconciliation worker started: poll_seconds=%s batch_size=%s",
        settings.WECHAT_PAY_RECONCILE_POLL_SECONDS,
        settings.WECHAT_PAY_RECONCILE_BATCH_SIZE,
    )
    while True:
        started = time.monotonic()
        payment_reconciliation_metrics.runs += 1
        payment_reconciliation_metrics.last_started_at = datetime.now(
            timezone.utc
        ).isoformat()
        try:
            batch = await active_service.reconcile_batch(after_id=after_id)
            after_id = batch.next_after_id
            payment_reconciliation_metrics.scanned += batch.scanned
            payment_reconciliation_metrics.synchronized += batch.synchronized
            payment_reconciliation_metrics.changed += batch.changed
            payment_reconciliation_metrics.failures += batch.failed
            payment_reconciliation_metrics.queue_depth = (
                await active_service.pending_count()
            )
            payment_reconciliation_metrics.last_error_type = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            payment_reconciliation_metrics.failures += 1
            payment_reconciliation_metrics.last_error_type = type(exc).__name__
            logger.error(
                "payment reconciliation iteration failed: exception_type=%s",
                type(exc).__name__,
            )
        finally:
            payment_reconciliation_metrics.last_finished_at = datetime.now(
                timezone.utc
            ).isoformat()
            payment_reconciliation_metrics.last_runtime_seconds = round(
                time.monotonic() - started, 6
            )
        await asyncio.sleep(settings.WECHAT_PAY_RECONCILE_POLL_SECONDS)


__all__ = [
    "PaymentReconciliationBatch",
    "PaymentReconciliationMetrics",
    "PaymentReconciliationService",
    "payment_reconciliation_metrics",
    "payment_reconciliation_worker_loop",
]
