"""Idempotent batch-level retention cleanup for human-resources data."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import exists, func, select, update

from app.adapter.database import get_db_ctx
from app.domain.order.src.index import Order
from app.domain.plan.src.index import Plan
from app.domain.renshe.src.index import (
    RensheApplication,
    RensheApplicationVersion,
    RensheAuditLog,
    RensheCleanupRun,
    RensheExportJob,
    RensheExportVolume,
    RensheMaterial,
    RensheRefundRequest,
)
from app.integrations.renshe_storage import RensheObjectStorage
from app.port.config import settings
from app.port.exceptions import ConflictException, NotFoundException
from app.schemas.renshe import RensheCleanupRunResponse


logger = logging.getLogger(__name__)
ACTIVE_REFUND_STATUSES = ("requested", "approved", "processing", "failed")
ACTIVE_EXPORT_STATUSES = ("queued", "running")


class RensheCleanupService:
    def __init__(self, storage: RensheObjectStorage | None = None) -> None:
        self.storage = storage or RensheObjectStorage()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    async def list_runs(self, plan_id: int) -> list[RensheCleanupRunResponse]:
        async with get_db_ctx() as db:
            plan = await db.get(Plan, plan_id)
            if plan is None or plan.product_type != "RS-ZY":
                raise NotFoundException("人社报名批次")
            rows = (
                await db.execute(
                    select(RensheCleanupRun)
                    .where(RensheCleanupRun.plan_id == plan_id)
                    .order_by(RensheCleanupRun.run_no.desc())
                )
            ).scalars().all()
            return [RensheCleanupRunResponse.model_validate(row) for row in rows]

    async def retry_run(
        self, *, run_id: int, super_admin_id: int
    ) -> RensheCleanupRunResponse:
        async with get_db_ctx() as db:
            run = await db.scalar(
                select(RensheCleanupRun)
                .where(RensheCleanupRun.id == run_id)
                .with_for_update()
            )
            if run is None:
                raise NotFoundException("人社批次清理任务")
            if run.status != "failed":
                raise ConflictException("只有失败的清理任务可以重试")
            now = self._now()
            if await self._active_refund_count(db, run.plan_id):
                run.status = "paused"
                run.paused_reason = "active_refunds"
                run.due_at = now + timedelta(days=7)
            else:
                run.status = "scheduled"
                run.paused_reason = None
                run.due_at = now
            run.started_at = None
            run.finished_at = None
            run.heartbeat_at = None
            run.retry_count += 1
            run.last_error = None
            db.add(
                RensheAuditLog(
                    actor_type="admin",
                    actor_id=super_admin_id,
                    action="cleanup.retry",
                    object_type="cleanup_run",
                    object_id=run.id,
                    result="succeeded",
                    summary={"plan_id": run.plan_id, "retry_count": run.retry_count},
                )
            )
            await db.commit()
            await db.refresh(run)
            return RensheCleanupRunResponse.model_validate(run)

    async def process_next_run(self) -> bool:
        await self._recover_stale_runs()
        resumed = await self._resume_one_paused_run()
        run_id = await self._claim_due_run()
        if run_id is None:
            return resumed
        heartbeat = asyncio.create_task(self._heartbeat_loop(run_id))
        try:
            await self._run_claimed_cleanup(run_id)
        except Exception as exc:
            logger.exception("human-resources cleanup failed: run_id=%s", run_id)
            await self._mark_failed(run_id, exc)
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
        return True

    async def _resume_one_paused_run(self) -> bool:
        async with get_db_ctx() as db:
            active_refund_exists = exists(
                select(RensheRefundRequest.id)
                .join(
                    RensheApplication,
                    RensheApplication.id == RensheRefundRequest.application_id,
                )
                .where(
                    RensheApplication.plan_id == RensheCleanupRun.plan_id,
                    RensheRefundRequest.status.in_(ACTIVE_REFUND_STATUSES),
                )
            )
            run = await db.scalar(
                select(RensheCleanupRun)
                .where(
                    RensheCleanupRun.status == "paused",
                    ~active_refund_exists,
                )
                .order_by(RensheCleanupRun.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if run is None:
                return False
            now = self._now()
            run.status = "scheduled"
            run.paused_reason = None
            run.due_at = now + timedelta(days=7)
            run.rebase_count += 1
            plan = await db.get(Plan, run.plan_id)
            if plan is not None:
                plan.cleanup_due_at = run.due_at
            db.add(
                RensheAuditLog(
                    actor_type="system",
                    actor_id=None,
                    action="cleanup.rebase",
                    object_type="cleanup_run",
                    object_id=run.id,
                    result="succeeded",
                    summary={"plan_id": run.plan_id, "rebase_count": run.rebase_count},
                )
            )
            await db.commit()
            return True

    async def _claim_due_run(self) -> int | None:
        now = self._now()
        async with get_db_ctx() as db:
            candidates = (
                await db.execute(
                    select(RensheCleanupRun.id, RensheCleanupRun.plan_id)
                    .where(
                        RensheCleanupRun.status == "scheduled",
                        RensheCleanupRun.due_at <= now,
                    )
                    .order_by(RensheCleanupRun.due_at, RensheCleanupRun.id)
                    .limit(1)
                )
            ).all()
            if not candidates:
                return None
            run_id, plan_id = candidates[0]

            plan = await db.scalar(
                select(Plan).where(Plan.id == plan_id).with_for_update()
            )
            if plan is None:
                raise NotFoundException("人社报名批次")
            run = await db.scalar(
                select(RensheCleanupRun)
                .where(
                    RensheCleanupRun.id == run_id,
                    RensheCleanupRun.status == "scheduled",
                    RensheCleanupRun.due_at <= now,
                )
                .with_for_update(skip_locked=True)
            )
            if run is None:
                return None
            if await self._active_refund_count(db, run.plan_id):
                run.status = "paused"
                run.paused_reason = "active_refunds"
                await db.commit()
                return None
            if await self._active_export_count(db, run.plan_id):
                return None
            run.status = "running"
            run.started_at = now
            run.heartbeat_at = now
            run.finished_at = None
            run.last_error = None
            await db.commit()
            return run.id

    async def _run_claimed_cleanup(self, run_id: int) -> None:
        async with get_db_ctx() as db:
            run = await db.get(RensheCleanupRun, run_id)
            if run is None or run.status != "running":
                raise ConflictException("清理任务未处于运行状态")
            application_ids = list(
                (
                    await db.execute(
                        select(RensheApplication.id).where(
                            RensheApplication.plan_id == run.plan_id
                        )
                    )
                ).scalars().all()
            )
            material_rows = (
                await db.execute(
                    select(RensheMaterial.id, RensheMaterial.storage_key).where(
                        RensheMaterial.application_id.in_(application_ids),
                        RensheMaterial.is_deleted.is_(False),
                    )
                )
            ).all() if application_ids else []
            volume_rows = (
                await db.execute(
                    select(RensheExportVolume.id, RensheExportVolume.storage_key)
                    .join(
                        RensheExportJob,
                        RensheExportJob.id == RensheExportVolume.job_id,
                    )
                    .where(
                        RensheExportJob.plan_id == run.plan_id,
                        RensheExportVolume.storage_key.is_not(None),
                    )
                )
            ).all()
            storage_keys = [row.storage_key for row in (*material_rows, *volume_rows)]

        await self.storage.delete_many(storage_keys)

        async with get_db_ctx() as db:
            run = await self._lock_cleanup_mutation_rows(
                db, application_ids=application_ids, run_id=run_id
            )
            if run is None or run.status != "running":
                raise ConflictException("清理任务状态已变化")
            now = self._now()
            if application_ids:
                await db.execute(
                    update(RensheMaterial)
                    .where(RensheMaterial.application_id.in_(application_ids))
                    .values(
                        is_deleted=True,
                        deleted_at=now,
                        source_storage_key=None,
                    )
                )
                await db.execute(
                    update(RensheApplicationVersion)
                    .where(RensheApplicationVersion.application_id.in_(application_ids))
                    .values(
                        realname_snapshot={},
                        student_snapshot={},
                        form_data={},
                        sensitive_cleared_at=now,
                    )
                )
                await db.execute(
                    update(RensheApplication)
                    .where(RensheApplication.id.in_(application_ids))
                    .values(draft_data=None, sensitive_cleared_at=now)
                )
                await db.execute(
                    update(Order)
                    .where(Order.application_id.in_(application_ids))
                    .values(
                        candidate_name="***",
                        candidate_phone="***",
                        candidate_idcard="***",
                        attachments=None,
                    )
                )
            if volume_rows:
                await db.execute(
                    update(RensheExportVolume)
                    .where(RensheExportVolume.id.in_([row.id for row in volume_rows]))
                    .values(storage_key=None)
                )
            run.status = "succeeded"
            run.finished_at = now
            run.heartbeat_at = now
            run.paused_reason = None
            db.add(
                RensheAuditLog(
                    actor_type="system",
                    actor_id=None,
                    action="cleanup.execute",
                    object_type="cleanup_run",
                    object_id=run.id,
                    result="succeeded",
                    summary={
                        "plan_id": run.plan_id,
                        "applications": len(application_ids),
                        "materials": len(material_rows),
                        "export_volumes": len(volume_rows),
                    },
                )
            )
            await db.commit()

    @staticmethod
    async def _lock_cleanup_mutation_rows(
        db, *, application_ids: list[int], run_id: int
    ):
        if application_ids:
            await db.execute(
                select(Order.id)
                .where(Order.application_id.in_(application_ids))
                .order_by(Order.application_id, Order.id)
                .with_for_update()
            )
            await db.execute(
                select(RensheApplication.id)
                .where(RensheApplication.id.in_(application_ids))
                .order_by(RensheApplication.id)
                .with_for_update()
            )
        return await db.scalar(
            select(RensheCleanupRun)
            .where(RensheCleanupRun.id == run_id)
            .with_for_update()
        )

    async def _mark_failed(self, run_id: int, exc: Exception) -> None:
        safe_error = f"{type(exc).__name__}: {str(exc)[:1000]}"
        async with get_db_ctx() as db:
            run = await db.scalar(
                select(RensheCleanupRun)
                .where(RensheCleanupRun.id == run_id)
                .with_for_update()
            )
            if run is None or run.status == "succeeded":
                return
            now = self._now()
            run.status = "failed"
            run.finished_at = now
            run.heartbeat_at = now
            run.last_error = safe_error
            db.add(
                RensheAuditLog(
                    actor_type="system",
                    actor_id=None,
                    action="cleanup.execute",
                    object_type="cleanup_run",
                    object_id=run.id,
                    result="failed",
                    summary={"plan_id": run.plan_id, "error_type": type(exc).__name__},
                )
            )
            await db.commit()

    async def _recover_stale_runs(self) -> None:
        cutoff = self._now() - timedelta(
            seconds=max(60, settings.RENSHE_WORKER_POLL_SECONDS * 12)
        )
        async with get_db_ctx() as db:
            await db.execute(
                update(RensheCleanupRun)
                .where(
                    RensheCleanupRun.status == "running",
                    RensheCleanupRun.heartbeat_at < cutoff,
                )
                .values(
                    status="failed",
                    finished_at=self._now(),
                    last_error="WorkerHeartbeatLost: 清理工作进程心跳中断",
                )
            )
            await db.commit()

    async def _heartbeat_loop(self, run_id: int) -> None:
        interval = max(5, settings.RENSHE_WORKER_POLL_SECONDS)
        while True:
            await asyncio.sleep(interval)
            async with get_db_ctx() as db:
                await db.execute(
                    update(RensheCleanupRun)
                    .where(
                        RensheCleanupRun.id == run_id,
                        RensheCleanupRun.status == "running",
                    )
                    .values(heartbeat_at=self._now())
                )
                await db.commit()

    @staticmethod
    async def _active_refund_count(db, plan_id: int) -> int:
        return (
            await db.scalar(
                select(func.count())
                .select_from(RensheRefundRequest)
                .join(
                    RensheApplication,
                    RensheApplication.id == RensheRefundRequest.application_id,
                )
                .where(
                    RensheApplication.plan_id == plan_id,
                    RensheRefundRequest.status.in_(ACTIVE_REFUND_STATUSES),
                )
            )
            or 0
        )

    @staticmethod
    async def _active_export_count(db, plan_id: int) -> int:
        return (
            await db.scalar(
                select(func.count())
                .select_from(RensheExportJob)
                .where(
                    RensheExportJob.plan_id == plan_id,
                    RensheExportJob.status.in_(ACTIVE_EXPORT_STATUSES),
                )
            )
            or 0
        )


async def renshe_cleanup_worker_loop() -> None:
    service = RensheCleanupService()
    while True:
        try:
            processed = await service.process_next_run()
        except Exception:
            logger.exception("human-resources cleanup worker iteration failed")
            processed = False
        if not processed:
            await asyncio.sleep(settings.RENSHE_WORKER_POLL_SECONDS)
