"""Recover NISP material deadlines and already-authorized refunds."""

import asyncio
import logging

from sqlalchemy import select

from app.adapter.database import get_db_ctx
from app.domain.nisp.src import NispRefundRequest
from app.port.config import settings
from app.services.nisp_registration import NispRegistrationService
from app.services.nisp_refund import NispRefundService

logger = logging.getLogger(__name__)


async def nisp_lifecycle_worker_loop():
    last_refund_id = 0
    while True:
        try:
            await NispRegistrationService().process_resubmission_timeouts()
        except Exception as exc:
            logger.error("NISP deadline processing failed: %s", type(exc).__name__)
        try:
            async with get_db_ctx() as db:
                query = select(NispRefundRequest.id).where(
                    NispRefundRequest.status.in_(("approved", "processing", "failed")),
                    NispRefundRequest.approved_by_admin_id.is_not(None),
                    NispRefundRequest.out_refund_no.is_not(None),
                    NispRefundRequest.id > last_refund_id,
                )
                if not settings.WECHAT_PAY_ENABLED:
                    query = query.where(NispRefundRequest.amount_cents == 0)
                ids = (await db.scalars(query.order_by(NispRefundRequest.id).limit(50))).all()
            last_refund_id = ids[-1] if ids else 0
            service = NispRefundService()
            for refund_id in ids:
                try:
                    await service.reconcile(refund_id)
                except Exception as exc:
                    logger.warning("NISP refund reconciliation failed: id=%s type=%s",
                                   refund_id, type(exc).__name__)
        except Exception as exc:
            logger.error("NISP refund scan failed: %s", type(exc).__name__)
        await asyncio.sleep(30)
