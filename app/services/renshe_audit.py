"""Small helpers for audit events that happen outside a domain transaction."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, or_, select

from app.adapter.database import get_db_ctx
from app.domain.renshe.src.index import (
    RensheApplication,
    RensheAuditLog,
    RensheCleanupRun,
    RensheExportJob,
    RensheExportVolume,
    RensheMaterial,
    RensheRefundRequest,
    RensheReview,
    RensheReviewCorrection,
)
from app.schemas.common import PaginatedData
from app.schemas.renshe import RensheAuditLogResponse
from app.port.exceptions import BusinessException
from app.utils.audit import sanitize_audit_summary


logger = logging.getLogger(__name__)


async def record_best_effort_audit(
    *,
    actor_type: str,
    actor_id: int | None,
    action: str,
    object_type: str,
    object_id: int,
    result: str,
    summary: dict[str, Any] | None = None,
    application_id: int | None = None,
    version_id: int | None = None,
    material_id: int | None = None,
    ip_address: str | None = None,
) -> None:
    """Persist an audit row without making authentication depend on logging.

    Domain mutations write their audit row in the same transaction.  Login and
    logout happen at the edge of a transaction, so they use this helper.  A
    temporary audit-storage outage is logged without exposing the payload or
    blocking an otherwise successful token exchange; the deployment health
    check still reports a failed database dependency.
    """

    try:
        async with get_db_ctx() as db:
            db.add(
                RensheAuditLog(
                    actor_type=actor_type,
                    actor_id=actor_id,
                    action=action,
                    object_type=object_type,
                    object_id=object_id,
                    application_id=application_id,
                    version_id=version_id,
                    material_id=material_id,
                    ip_address=ip_address,
                    result=result,
                    summary=summary,
                )
            )
            await db.commit()
    except Exception:
        logger.warning(
            "renshe audit write failed action=%s object_type=%s object_id=%s",
            action,
            object_type,
            object_id,
        )


class RensheAuditQueryService:
    """Read-only, redacted query surface for append-only human-resources audit."""

    async def list_logs(
        self,
        *,
        admin_id: int,
        ip_address: str | None,
        plan_id: int | None,
        application_id: int | None,
        actor_admin_id: int | None,
        action: str | None,
        result: str | None,
        started_at: datetime | None,
        ended_at: datetime | None,
        page: int,
        page_size: int,
    ) -> PaginatedData[RensheAuditLogResponse]:
        if any(value is not None and value.tzinfo is None for value in (started_at, ended_at)):
            raise BusinessException("审计查询时间必须包含时区")
        if started_at and ended_at and started_at > ended_at:
            raise BusinessException("审计查询开始时间不能晚于结束时间")

        filters = []
        if plan_id is not None:
            filters.append(self._plan_scope(plan_id))
        if application_id is not None:
            filters.append(RensheAuditLog.application_id == application_id)
        if actor_admin_id is not None:
            filters.append(
                and_(
                    RensheAuditLog.actor_type == "admin",
                    RensheAuditLog.actor_id == actor_admin_id,
                )
            )
        if action:
            filters.append(RensheAuditLog.action == action.strip())
        if result:
            filters.append(RensheAuditLog.result == result)
        if started_at is not None:
            filters.append(RensheAuditLog.created_at >= started_at)
        if ended_at is not None:
            filters.append(RensheAuditLog.created_at <= ended_at)

        query_summary = {
            "plan_id": plan_id,
            "application_id": application_id,
            "actor_admin_id": actor_admin_id,
            "action": action.strip() if action else None,
            "result": result,
            "has_started_at": started_at is not None,
            "has_ended_at": ended_at is not None,
            "page": page,
            "page_size": page_size,
        }
        try:
            async with get_db_ctx() as db:
                base = select(RensheAuditLog).where(*filters)
                total = (
                    await db.scalar(select(func.count()).select_from(base.subquery()))
                    or 0
                )
                rows = (
                    await db.execute(
                        base.order_by(
                            RensheAuditLog.created_at.desc(),
                            RensheAuditLog.id.desc(),
                        )
                        .offset((page - 1) * page_size)
                        .limit(page_size)
                    )
                ).scalars().all()
                items = [self._response(row) for row in rows]
                db.add(
                    RensheAuditLog(
                        actor_type="admin",
                        actor_id=admin_id,
                        action="audit.query",
                        object_type="audit_log",
                        object_id=admin_id,
                        ip_address=ip_address,
                        result="succeeded",
                        summary=query_summary,
                    )
                )
                await db.commit()
                return PaginatedData[RensheAuditLogResponse](
                    items=items,
                    total=total,
                    page=page,
                    page_size=page_size,
                )
        except Exception as exc:
            await record_best_effort_audit(
                actor_type="admin",
                actor_id=admin_id,
                action="audit.query",
                object_type="audit_log",
                object_id=admin_id,
                ip_address=ip_address,
                result="failed",
                summary={
                    **query_summary,
                    "error_type": type(exc).__name__,
                },
            )
            raise

    @staticmethod
    def _response(row: RensheAuditLog) -> RensheAuditLogResponse:
        response = RensheAuditLogResponse.model_validate(row)
        # Re-sanitize on read so a legacy/imported row cannot bypass the current
        # persistence hooks and leak through the admin API.
        response.summary = sanitize_audit_summary(row.summary)
        return response

    @staticmethod
    def _plan_scope(plan_id: int):
        application_ids = select(RensheApplication.id).where(
            RensheApplication.plan_id == plan_id
        )
        refund_ids = select(RensheRefundRequest.id).where(
            RensheRefundRequest.application_id.in_(application_ids)
        )
        material_ids = select(RensheMaterial.id).where(
            RensheMaterial.application_id.in_(application_ids)
        )
        review_ids = select(RensheReview.id).where(
            RensheReview.application_id.in_(application_ids)
        )
        correction_ids = select(RensheReviewCorrection.id).where(
            RensheReviewCorrection.application_id.in_(application_ids)
        )
        cleanup_ids = select(RensheCleanupRun.id).where(
            RensheCleanupRun.plan_id == plan_id
        )
        export_job_ids = select(RensheExportJob.id).where(
            RensheExportJob.plan_id == plan_id
        )
        export_volume_ids = select(RensheExportVolume.id).where(
            RensheExportVolume.job_id.in_(export_job_ids)
        )
        return or_(
            RensheAuditLog.application_id.in_(application_ids),
            and_(
                RensheAuditLog.object_type == "plan",
                RensheAuditLog.object_id == plan_id,
            ),
            and_(
                RensheAuditLog.object_type == "refund",
                RensheAuditLog.object_id.in_(refund_ids),
            ),
            and_(
                RensheAuditLog.object_type == "material",
                RensheAuditLog.object_id.in_(material_ids),
            ),
            and_(
                RensheAuditLog.object_type == "review",
                RensheAuditLog.object_id.in_(review_ids),
            ),
            and_(
                RensheAuditLog.object_type == "review_correction",
                RensheAuditLog.object_id.in_(correction_ids),
            ),
            and_(
                RensheAuditLog.object_type == "cleanup_run",
                RensheAuditLog.object_id.in_(cleanup_ids),
            ),
            and_(
                RensheAuditLog.object_type == "export_job",
                RensheAuditLog.object_id.in_(export_job_ids),
            ),
            and_(
                RensheAuditLog.object_type == "export_volume",
                RensheAuditLog.object_id.in_(export_volume_ids),
            ),
        )


__all__ = ["RensheAuditQueryService", "record_best_effort_audit"]
