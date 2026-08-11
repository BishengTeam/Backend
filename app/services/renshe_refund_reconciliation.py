"""Restart-safe submission and reconciliation of human-resources refunds."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select

from app.adapter.database import get_db_ctx
from app.domain.renshe.src.index import RensheRefundRequest
from app.port.config import settings
from app.services.renshe_refund import RensheRefundService, SYSTEM_REFUND_KINDS


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RensheRefundReconciliationBatch:
    scanned: int = 0
    synchronized: int = 0
    changed: int = 0
    failed: int = 0
    next_after_id: int = 0


@dataclass(slots=True)
class RensheRefundReconciliationMetrics:
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


renshe_refund_reconciliation_metrics = RensheRefundReconciliationMetrics()


class RensheRefundReconciliationService:
    def __init__(self, refund_service: RensheRefundService | None = None) -> None:
        self.refund_service = refund_service or RensheRefundService()

    @staticmethod
    def _eligible_condition(now: datetime):
        query_cutoff = now - timedelta(
            seconds=settings.WECHAT_PAY_REFUND_RECONCILE_AFTER_SECONDS
        )
        return or_(
            and_(
                RensheRefundRequest.status == "requested",
                RensheRefundRequest.request_kind.in_(SYSTEM_REFUND_KINDS),
            ),
            RensheRefundRequest.status == "approved",
            and_(
                RensheRefundRequest.status == "processing",
                RensheRefundRequest.updated_at <= query_cutoff,
            ),
        )

    async def pending_count(self) -> int:
        async with get_db_ctx() as db:
            return int(
                await db.scalar(
                    select(func.count())
                    .select_from(RensheRefundRequest)
                    .where(self._eligible_condition(datetime.now(timezone.utc)))
                )
                or 0
            )

    async def reconcile_batch(
        self,
        *,
        after_id: int = 0,
        limit: int | None = None,
    ) -> RensheRefundReconciliationBatch:
        batch_limit = limit or settings.WECHAT_PAY_REFUND_RECONCILE_BATCH_SIZE
        if batch_limit < 1 or batch_limit > 500:
            raise ValueError("refund reconciliation limit must be between 1 and 500")
        now = datetime.now(timezone.utc)
        async with get_db_ctx() as db:
            refund_ids = list(
                (
                    await db.scalars(
                        select(RensheRefundRequest.id)
                        .where(
                            RensheRefundRequest.id > after_id,
                            self._eligible_condition(now),
                        )
                        .order_by(RensheRefundRequest.id.asc())
                        .limit(batch_limit)
                    )
                ).all()
            )

        result = RensheRefundReconciliationBatch(scanned=len(refund_ids))
        for refund_id in refund_ids:
            try:
                before_status = await self._status(refund_id)
                synchronized = await self.refund_service.reconcile_refund(refund_id)
                result.synchronized += 1
                if synchronized.status != before_status:
                    result.changed += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                result.failed += 1
                logger.error(
                    "refund reconciliation item failed: refund_id=%s exception_type=%s",
                    refund_id,
                    type(exc).__name__,
                )
        result.next_after_id = (
            refund_ids[-1] if len(refund_ids) == batch_limit else 0
        )
        return result

    @staticmethod
    async def _status(refund_id: int) -> str | None:
        async with get_db_ctx() as db:
            return await db.scalar(
                select(RensheRefundRequest.status).where(
                    RensheRefundRequest.id == refund_id
                )
            )


async def renshe_refund_reconciliation_worker_loop(
    service: RensheRefundReconciliationService | None = None,
) -> None:
    active_service = service or RensheRefundReconciliationService()
    after_id = 0
    logger.info(
        "refund reconciliation worker started: poll_seconds=%s batch_size=%s",
        settings.WECHAT_PAY_REFUND_RECONCILE_POLL_SECONDS,
        settings.WECHAT_PAY_REFUND_RECONCILE_BATCH_SIZE,
    )
    while True:
        started = time.monotonic()
        metrics = renshe_refund_reconciliation_metrics
        metrics.runs += 1
        metrics.last_started_at = datetime.now(timezone.utc).isoformat()
        try:
            batch = await active_service.reconcile_batch(after_id=after_id)
            after_id = batch.next_after_id
            metrics.scanned += batch.scanned
            metrics.synchronized += batch.synchronized
            metrics.changed += batch.changed
            metrics.failures += batch.failed
            metrics.queue_depth = await active_service.pending_count()
            metrics.last_error_type = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            metrics.failures += 1
            metrics.last_error_type = type(exc).__name__
            logger.error(
                "refund reconciliation iteration failed: exception_type=%s",
                type(exc).__name__,
            )
        finally:
            metrics.last_finished_at = datetime.now(timezone.utc).isoformat()
            metrics.last_runtime_seconds = round(time.monotonic() - started, 6)
        await asyncio.sleep(settings.WECHAT_PAY_REFUND_RECONCILE_POLL_SECONDS)


__all__ = [
    "RensheRefundReconciliationBatch",
    "RensheRefundReconciliationMetrics",
    "RensheRefundReconciliationService",
    "renshe_refund_reconciliation_metrics",
    "renshe_refund_reconciliation_worker_loop",
]
