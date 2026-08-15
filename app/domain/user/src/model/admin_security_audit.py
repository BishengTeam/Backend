"""Permanent, append-only administrator security audit records."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base
from app.utils.audit import redact_sensitive_text, sanitize_audit_summary


ADMIN_SECURITY_AUDIT_RESULTS = ("succeeded", "failed")
ADMIN_IDEMPOTENCY_DIGEST_LENGTH = 64
ADMIN_CREDENTIAL_IDEMPOTENCY_ACTIONS = (
    "admin_account.create",
    "admin_account.enable",
    "admin_account.password_reset",
)
ADMIN_CREDENTIAL_IDEMPOTENCY_PREDICATE = (
    "result = 'succeeded' AND actor_admin_id IS NOT NULL "
    "AND idempotency_key_hash IS NOT NULL AND action IN "
    f"({', '.join(repr(action) for action in ADMIN_CREDENTIAL_IDEMPOTENCY_ACTIONS)})"
)


class AdminSecurityAudit(Base):
    """Security event independent from business-domain audit streams."""

    __tablename__ = "admin_security_audit"
    __table_args__ = (
        CheckConstraint(
            "result IN ('succeeded', 'failed')",
            name="ck_admin_security_audit_result",
        ),
        CheckConstraint(
            "idempotency_key_hash IS NULL "
            f"OR length(idempotency_key_hash) = {ADMIN_IDEMPOTENCY_DIGEST_LENGTH}",
            name="ck_admin_security_audit_idempotency_hash_length",
        ),
        Index(
            "ix_admin_security_audit_actor",
            "actor_admin_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_admin_security_audit_target",
            "target_admin_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_admin_security_audit_action_result",
            "action",
            "result",
            "created_at",
            "id",
        ),
        Index(
            "ix_admin_security_audit_username",
            "username",
            "created_at",
            "id",
        ),
        Index("ix_admin_security_audit_request_id", "request_id", "id"),
        Index(
            "uq_admin_security_audit_credential_idempotency",
            "actor_admin_id",
            "action",
            "idempotency_key_hash",
            unique=True,
            postgresql_where=text(ADMIN_CREDENTIAL_IDEMPOTENCY_PREDICATE),
            sqlite_where=text(ADMIN_CREDENTIAL_IDEMPOTENCY_PREDICATE),
        ),
        Index("ix_admin_security_audit_created", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_admin_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="RESTRICT")
    )
    target_admin_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="RESTRICT")
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64))
    username: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    # Only a one-way digest is retained.  The client key is correlation data,
    # not a bearer credential, but keeping the raw value is unnecessary.
    idempotency_key_hash: Mapped[str | None] = mapped_column(
        String(ADMIN_IDEMPOTENCY_DIGEST_LENGTH), nullable=True
    )
    source_ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    summary: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


@event.listens_for(AdminSecurityAudit, "before_insert")
def _sanitize_admin_security_audit(_mapper, _connection, target) -> None:
    target.summary = sanitize_audit_summary(target.summary)
    target.user_agent = redact_sensitive_text(target.user_agent)
    if target.username is not None:
        target.username = target.username.strip().lower()


@event.listens_for(AdminSecurityAudit, "before_update")
def _reject_admin_security_audit_update(_mapper, _connection, _target) -> None:
    raise ValueError("administrator security audit rows are append-only")


@event.listens_for(AdminSecurityAudit, "before_delete")
def _reject_admin_security_audit_delete(_mapper, _connection, _target) -> None:
    raise ValueError("administrator security audit rows are append-only")


__all__ = [
    "ADMIN_CREDENTIAL_IDEMPOTENCY_ACTIONS",
    "ADMIN_CREDENTIAL_IDEMPOTENCY_PREDICATE",
    "ADMIN_IDEMPOTENCY_DIGEST_LENGTH",
    "ADMIN_SECURITY_AUDIT_RESULTS",
    "AdminSecurityAudit",
]
