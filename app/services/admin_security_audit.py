"""Append-only administrator security audit recording and compatibility query."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Integer, case, cast, func, literal, or_, select, union_all
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapter.database import get_db_ctx
from app.domain.renshe.src.index import RensheAuditLog
from app.domain.user.src.index import AdminSecurityAudit
from app.port.exceptions import BusinessException
from app.schemas.admin_settings import AdminSecurityAuditListItem
from app.schemas.common import PaginatedData
from app.utils.audit import sanitize_audit_summary, redact_sensitive_text


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AdminAuditContext:
    request_id: str | None = None
    source_ip: str | None = None
    user_agent: str | None = None

    def normalized(self) -> "AdminAuditContext":
        return AdminAuditContext(
            request_id=_bounded(self.request_id, 64),
            source_ip=_bounded(self.source_ip, 45),
            user_agent=_bounded(redact_sensitive_text(self.user_agent), 512),
        )


def _bounded(value: str | None, length: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized[:length] or None


def admin_idempotency_digest(value: str) -> str:
    """Return the persistence-safe identity of a client idempotency key."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AdminSecurityAuditService:
    """The only write/query service for administrator security events.

    Successful account mutations call :meth:`append` inside their own
    transaction so the mutation cannot commit without its audit event.
    ``record_best_effort`` is reserved for failure paths where the original
    transaction has already rolled back.
    """

    @staticmethod
    def append(
        db: AsyncSession,
        *,
        action: str,
        result: str,
        reason_code: str | None = None,
        actor_admin_id: int | None = None,
        target_admin_id: int | None = None,
        username: str | None = None,
        context: AdminAuditContext | None = None,
        summary: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> AdminSecurityAudit:
        if result not in {"succeeded", "failed"}:
            raise ValueError("invalid administrator audit result")
        metadata = (context or AdminAuditContext()).normalized()
        row = AdminSecurityAudit(
            action=_bounded(action, 64) or "unknown",
            result=result,
            reason_code=_bounded(reason_code, 64),
            actor_admin_id=actor_admin_id,
            target_admin_id=target_admin_id,
            username=_bounded(username.lower() if username else None, 64),
            request_id=metadata.request_id,
            idempotency_key_hash=(
                admin_idempotency_digest(idempotency_key)
                if idempotency_key
                else None
            ),
            source_ip=metadata.source_ip,
            user_agent=metadata.user_agent,
            summary=sanitize_audit_summary(summary),
        )
        db.add(row)
        return row

    async def record_best_effort(self, **kwargs: Any) -> None:
        try:
            await self.record(**kwargs)
        except Exception:
            # Do not log the supplied context or summary: either may contain a
            # username, address or credentials from a failed edge request.
            logger.warning(
                "administrator security audit write failed action=%s",
                kwargs.get("action", "unknown"),
            )

    async def record(self, **kwargs: Any) -> None:
        async with get_db_ctx() as db:
            self.append(db, **kwargs)
            await db.commit()

    async def list_logs(
        self,
        *,
        actor_admin_id: int | None,
        target_admin_id: int | None,
        action: str | None,
        result: str | None,
        username: str | None,
        request_id: str | None,
        started_at: datetime | None,
        ended_at: datetime | None,
        page: int,
        page_size: int,
    ) -> PaginatedData[AdminSecurityAuditListItem]:
        if any(value is not None and value.tzinfo is None for value in (started_at, ended_at)):
            raise BusinessException("审计查询时间必须包含时区")
        if started_at and ended_at and started_at > ended_at:
            raise BusinessException("审计查询开始时间不能晚于结束时间")

        # Legacy administrator-account events were incorrectly written into
        # the human-resources audit table.  Include them as negative IDs so
        # callers receive one stable, collision-free read model without
        # copying or silently dropping permanent history.
        legacy_username = RensheAuditLog.summary["username"].as_string()
        legacy_request_id = RensheAuditLog.summary["request_id"].as_string()
        legacy_user_agent = RensheAuditLog.summary["user_agent"].as_string()
        legacy_reason = RensheAuditLog.summary["reason_code"].as_string()
        legacy_target = case(
            (RensheAuditLog.object_type == "admin_user", RensheAuditLog.object_id),
            else_=None,
        ).cast(Integer)

        current = select(
            AdminSecurityAudit.id.label("id"),
            AdminSecurityAudit.action.label("action"),
            AdminSecurityAudit.result.label("result"),
            AdminSecurityAudit.reason_code.label("reason_code"),
            AdminSecurityAudit.actor_admin_id.label("actor_admin_id"),
            AdminSecurityAudit.target_admin_id.label("target_admin_id"),
            AdminSecurityAudit.username.label("username"),
            AdminSecurityAudit.request_id.label("request_id"),
            AdminSecurityAudit.source_ip.label("source_ip"),
            AdminSecurityAudit.user_agent.label("user_agent"),
            AdminSecurityAudit.summary.label("summary"),
            AdminSecurityAudit.created_at.label("created_at"),
        )
        legacy = select(
            (-RensheAuditLog.id).label("id"),
            RensheAuditLog.action.label("action"),
            RensheAuditLog.result.label("result"),
            func.coalesce(legacy_reason, literal("legacy_event")).label("reason_code"),
            case(
                (RensheAuditLog.actor_type == "admin", RensheAuditLog.actor_id),
                else_=None,
            ).cast(Integer).label("actor_admin_id"),
            legacy_target.label("target_admin_id"),
            legacy_username.label("username"),
            legacy_request_id.label("request_id"),
            RensheAuditLog.ip_address.label("source_ip"),
            legacy_user_agent.label("user_agent"),
            cast(RensheAuditLog.summary, JSONB).label("summary"),
            RensheAuditLog.created_at.label("created_at"),
        ).where(
            or_(
                RensheAuditLog.action.like("admin_account.%"),
                RensheAuditLog.action.like("auth.admin_%"),
            )
        )
        combined = union_all(current, legacy).subquery("administrator_security_history")

        filters = []
        if actor_admin_id is not None:
            filters.append(combined.c.actor_admin_id == actor_admin_id)
        if target_admin_id is not None:
            filters.append(combined.c.target_admin_id == target_admin_id)
        if action:
            filters.append(combined.c.action == action.strip())
        if result:
            filters.append(combined.c.result == result)
        if username:
            filters.append(func.lower(combined.c.username) == username.strip().lower())
        if request_id:
            filters.append(combined.c.request_id == request_id.strip())
        if started_at is not None:
            filters.append(combined.c.created_at >= started_at)
        if ended_at is not None:
            filters.append(combined.c.created_at <= ended_at)

        async with get_db_ctx() as db:
            filtered = select(combined).where(*filters)
            total = await db.scalar(select(func.count()).select_from(filtered.subquery())) or 0
            rows = (
                await db.execute(
                    filtered.order_by(combined.c.created_at.desc(), combined.c.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).mappings().all()

        items = [
            AdminSecurityAuditListItem(
                **{
                    **dict(row),
                    "user_agent": redact_sensitive_text(row.get("user_agent")),
                    "summary": sanitize_audit_summary(row.get("summary")),
                }
            )
            for row in rows
        ]
        return PaginatedData[AdminSecurityAuditListItem](
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )


__all__ = [
    "AdminAuditContext",
    "AdminSecurityAuditService",
    "admin_idempotency_digest",
]
