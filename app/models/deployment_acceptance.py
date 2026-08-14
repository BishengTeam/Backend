"""Append-only deployment and production-UAT acceptance records."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


DEPLOYMENT_ACCEPTANCE_STATUSES = (
    "installed_pending_uat",
    "production_accepted",
)

DEPLOYMENT_EVIDENCE_TYPES = (
    "runtime_health",
    "runtime_readiness",
    "worker_heartbeat",
    "wechat_login",
    "uat_scope",
    "renshe_private_oss",
    "wechat_payment",
    "wechat_refund",
    "recovery_bundle",
    "backup_restore_config",
)

DEPLOYMENT_EVIDENCE_RESULTS = ("passed", "failed")


class DeploymentAcceptance(Base, TimestampMixin):
    """Current production-acceptance state for one immutable installation."""

    __tablename__ = "deployment_acceptance"
    __table_args__ = (
        CheckConstraint(
            "status IN ('installed_pending_uat', 'production_accepted')",
            name="ck_deployment_acceptance_status",
        ),
        CheckConstraint(
            "((status = 'installed_pending_uat' "
            "AND accepted_by_admin_id IS NULL AND accepted_at IS NULL "
            "AND evidence_summary_sha256 IS NULL) OR "
            "(status = 'production_accepted' "
            "AND accepted_by_admin_id IS NOT NULL AND accepted_at IS NOT NULL "
            "AND evidence_summary_sha256 IS NOT NULL))",
            name="ck_deployment_acceptance_completion",
        ),
    )

    installation_id: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="installed_pending_uat",
        server_default="installed_pending_uat",
        index=True,
    )
    backend_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    admin_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    release_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    recovery_object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    recovery_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    database_fingerprint_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    accepted_by_admin_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("admin_user.id", ondelete="RESTRICT"),
        nullable=True,
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    evidence_summary_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )


class DeploymentAcceptanceEvent(Base):
    """Immutable evidence or signature event for a deployment acceptance."""

    __tablename__ = "deployment_acceptance_event"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('evidence_recorded', 'acceptance_signed')",
            name="ck_deployment_acceptance_event_type",
        ),
        CheckConstraint(
            "result IS NULL OR result IN ('passed', 'failed')",
            name="ck_deployment_acceptance_event_result",
        ),
        CheckConstraint(
            "evidence_type IS NULL OR evidence_type IN ("
            "'runtime_health', 'runtime_readiness', 'worker_heartbeat', "
            "'wechat_login', 'uat_scope', 'renshe_private_oss', "
            "'wechat_payment', 'wechat_refund', 'recovery_bundle', "
            "'backup_restore_config')",
            name="ck_deployment_acceptance_evidence_type",
        ),
        CheckConstraint(
            "source IN ('bootstrap', 'system', 'uat_reconciler')",
            name="ck_deployment_acceptance_event_source",
        ),
        CheckConstraint(
            "((event_type = 'evidence_recorded' "
            "AND evidence_type IS NOT NULL AND result IS NOT NULL) OR "
            "(event_type = 'acceptance_signed' "
            "AND evidence_type IS NULL AND result IS NULL "
            "AND actor_admin_id IS NOT NULL))",
            name="ck_deployment_acceptance_event_shape",
        ),
        UniqueConstraint(
            "acceptance_id",
            "event_type",
            "evidence_sha256",
            name="uq_deployment_acceptance_event_digest",
        ),
        Index(
            "ix_deployment_acceptance_event_lookup",
            "acceptance_id",
            "evidence_type",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    acceptance_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("deployment_acceptance.id", ondelete="RESTRICT"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    result: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_admin_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("admin_user.id", ondelete="RESTRICT"),
        nullable=True,
    )
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
