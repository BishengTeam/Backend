from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin
from app.utils.audit import sanitize_audit_summary


APPLICATION_STATUSES = (
    "draft",
    "pending_payment",
    "pending_initial_review",
    "initial_rejected",
    "pending_external_review",
    "external_rejected",
    "external_approved",
    "closed",
)


class RensheApplication(Base, TimestampMixin):
    __tablename__ = "renshe_application"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'pending_payment', 'pending_initial_review', "
            "'initial_rejected', 'pending_external_review', "
            "'external_rejected', 'external_approved', 'closed')",
            name="ck_renshe_application_status",
        ),
        Index("ix_renshe_application_plan_status", "plan_id", "status"),
        Index("ix_renshe_application_user_status", "user_id", "status"),
        Index(
            "uq_renshe_application_active_user_plan",
            "user_id",
            "plan_id",
            unique=True,
            postgresql_where=text("status <> 'closed'"),
            sqlite_where=text("status <> 'closed'"),
        ),
    )

    plan_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("plan.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    current_version_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "renshe_application_version.id",
            name="fk_renshe_application_current_version",
            use_alter=True,
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="draft", server_default="draft", index=True
    )
    draft_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    initial_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    external_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    freeze_reason: Mapped[str | None] = mapped_column(String(64))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[str | None] = mapped_column(String(128))
    sensitive_cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RensheApplicationVersion(Base, TimestampMixin):
    __tablename__ = "renshe_application_version"
    __table_args__ = (
        UniqueConstraint(
            "application_id", "version_no", name="uq_renshe_version_application_no"
        ),
        Index("ix_renshe_version_application", "application_id", "version_no"),
    )

    application_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("renshe_application.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    submitted_by_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False
    )
    realname_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    student_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    form_data: Mapped[dict] = mapped_column(JSON, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sensitive_cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RensheMaterial(Base, TimestampMixin):
    __tablename__ = "renshe_material"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('id_card_front', 'id_card_back', 'portrait', "
            "'student_card', 'xuexin_registration', 'education_proof')",
            name="ck_renshe_material_kind",
        ),
        CheckConstraint("size_bytes > 0", name="ck_renshe_material_size_positive"),
        Index(
            "uq_renshe_material_version_kind",
            "version_id",
            "kind",
            unique=True,
            postgresql_where=text("version_id IS NOT NULL"),
            sqlite_where=text("version_id IS NOT NULL"),
        ),
        Index("ix_renshe_material_application", "application_id", "version_id"),
    )

    application_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("renshe_application.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("renshe_application_version.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_storage_key: Mapped[str | None] = mapped_column(String(512))
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    extension: Mapped[str] = mapped_column(String(8), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RensheReview(Base, TimestampMixin):
    __tablename__ = "renshe_review"
    __table_args__ = (
        CheckConstraint("stage IN ('initial', 'external')", name="ck_renshe_review_stage"),
        CheckConstraint(
            "decision IN ('approved', 'rejected')", name="ck_renshe_review_decision"
        ),
        CheckConstraint(
            "decision <> 'rejected' OR (reason IS NOT NULL AND length(trim(reason)) > 0)",
            name="ck_renshe_review_rejection_reason",
        ),
        Index("ix_renshe_review_application_stage", "application_id", "stage"),
    )

    application_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("renshe_application.id", ondelete="RESTRICT"), nullable=False
    )
    version_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("renshe_application_version.id", ondelete="RESTRICT"),
        nullable=False,
    )
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    required_changes: Mapped[list | None] = mapped_column(JSON)
    reviewer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="RESTRICT"), nullable=False
    )
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RensheReviewCorrection(Base, TimestampMixin):
    __tablename__ = "renshe_review_correction"
    __table_args__ = (
        CheckConstraint(
            "from_decision IN ('approved', 'rejected')",
            name="ck_renshe_correction_from_decision",
        ),
        CheckConstraint(
            "to_decision IN ('approved', 'rejected')",
            name="ck_renshe_correction_to_decision",
        ),
        CheckConstraint(
            "from_decision <> to_decision", name="ck_renshe_correction_changes_decision"
        ),
    )

    review_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("renshe_review.id", ondelete="RESTRICT"), nullable=False
    )
    application_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("renshe_application.id", ondelete="RESTRICT"), nullable=False
    )
    corrected_by_admin_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="RESTRICT"), nullable=False
    )
    from_decision: Mapped[str] = mapped_column(String(16), nullable=False)
    to_decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    state_before: Mapped[dict] = mapped_column(JSON, nullable=False)
    state_after: Mapped[dict] = mapped_column(JSON, nullable=False)


class RensheRefundRequest(Base, TimestampMixin):
    __tablename__ = "renshe_refund_request"
    __table_args__ = (
        CheckConstraint(
            "request_kind IN ('normal', 'exception', 'batch_cancel', 'batch_finalize')",
            name="ck_renshe_refund_kind",
        ),
        CheckConstraint(
            "status IN ('requested', 'approved', 'processing', 'succeeded', "
            "'rejected', 'failed')",
            name="ck_renshe_refund_status",
        ),
        CheckConstraint("amount_cents > 0", name="ck_renshe_refund_amount_positive"),
        Index("ix_renshe_refund_status_due", "status", "due_at"),
        Index("ix_renshe_refund_status_updated", "status", "updated_at"),
        Index(
            "uq_renshe_refund_active_order",
            "order_id",
            unique=True,
            postgresql_where=text(
                "status IN ('requested', 'approved', 'processing', 'failed')"
            ),
            sqlite_where=text(
                "status IN ('requested', 'approved', 'processing', 'failed')"
            ),
        ),
        Index(
            "uq_renshe_refund_succeeded_order",
            "order_id",
            unique=True,
            postgresql_where=text("status = 'succeeded'"),
            sqlite_where=text("status = 'succeeded'"),
        ),
    )

    application_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("renshe_application.id", ondelete="RESTRICT"), nullable=False
    )
    order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("order.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False
    )
    request_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_detail: Mapped[str | None] = mapped_column(Text)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="requested", server_default="requested"
    )
    previous_application_status: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approved_by_admin_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="RESTRICT")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    out_refund_no: Mapped[str | None] = mapped_column(String(64), unique=True)
    wechat_refund_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    processing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class RensheExportJob(Base, TimestampMixin):
    __tablename__ = "renshe_export_job"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_renshe_export_job_status",
        ),
        UniqueConstraint("plan_id", "generation_no", name="uq_renshe_export_generation"),
        Index("ix_renshe_export_plan_status", "plan_id", "status"),
    )

    plan_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("plan.id", ondelete="RESTRICT"), nullable=False
    )
    generation_no: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_by_admin_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="queued", server_default="queued"
    )
    candidate_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    candidate_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    volume_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)


class RensheExportVolume(Base, TimestampMixin):
    __tablename__ = "renshe_export_volume"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_renshe_export_volume_status",
        ),
        CheckConstraint(
            "size_bytes IS NULL OR size_bytes <= 10737418240",
            name="ck_renshe_export_volume_max_size",
        ),
        UniqueConstraint("job_id", "volume_no", name="uq_renshe_export_volume_no"),
    )

    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("renshe_export_job.id", ondelete="CASCADE"), nullable=False
    )
    volume_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="queued", server_default="queued"
    )
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    storage_key: Mapped[str | None] = mapped_column(String(512), unique=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class RensheExportItem(Base, TimestampMixin):
    __tablename__ = "renshe_export_item"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_renshe_export_item_status",
        ),
        UniqueConstraint("job_id", "application_id", name="uq_renshe_export_item_application"),
        Index("ix_renshe_export_item_job_status", "job_id", "status"),
    )

    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("renshe_export_job.id", ondelete="CASCADE"), nullable=False
    )
    volume_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("renshe_export_volume.id", ondelete="SET NULL")
    )
    application_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("renshe_application.id", ondelete="RESTRICT"), nullable=False
    )
    version_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("renshe_application_version.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="queued", server_default="queued"
    )
    estimated_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    last_error: Mapped[str | None] = mapped_column(Text)


class RensheCleanupRun(Base, TimestampMixin):
    __tablename__ = "renshe_cleanup_run"
    __table_args__ = (
        CheckConstraint(
            "status IN ('scheduled', 'running', 'paused', 'succeeded', 'failed')",
            name="ck_renshe_cleanup_status",
        ),
        UniqueConstraint("plan_id", "run_no", name="uq_renshe_cleanup_plan_run"),
        Index("ix_renshe_cleanup_status_due", "status", "due_at"),
    )

    plan_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("plan.id", ondelete="RESTRICT"), nullable=False
    )
    run_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="scheduled", server_default="scheduled"
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paused_reason: Mapped[str | None] = mapped_column(String(128))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    rebase_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)


class RensheAuditLog(Base, TimestampMixin):
    __tablename__ = "renshe_audit_log"
    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('user', 'admin', 'system')", name="ck_renshe_audit_actor_type"
        ),
        CheckConstraint(
            "result IN ('succeeded', 'failed')", name="ck_renshe_audit_result"
        ),
        Index("ix_renshe_audit_object", "object_type", "object_id"),
        Index("ix_renshe_audit_actor", "actor_type", "actor_id"),
        Index(
            "ix_renshe_audit_application_created",
            "application_id",
            "created_at",
        ),
        Index("ix_renshe_audit_result_created", "result", "created_at"),
    )

    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    object_type: Mapped[str] = mapped_column(String(32), nullable=False)
    object_id: Mapped[int] = mapped_column(Integer, nullable=False)
    application_id: Mapped[int | None] = mapped_column(Integer)
    version_id: Mapped[int | None] = mapped_column(Integer)
    material_id: Mapped[int | None] = mapped_column(Integer)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    summary: Mapped[dict | None] = mapped_column(JSON)


@event.listens_for(RensheAuditLog, "before_insert")
def _sanitize_renshe_audit_before_insert(_mapper, _connection, target) -> None:
    """Enforce the no-PII-in-audit contract at the persistence boundary."""

    target.summary = sanitize_audit_summary(target.summary)


@event.listens_for(RensheAuditLog, "before_update")
def _sanitize_renshe_audit_before_update(_mapper, _connection, target) -> None:
    # Audit rows are append-only in the service layer, but this guard also
    # protects maintenance scripts that may update a legacy row.
    target.summary = sanitize_audit_summary(target.summary)
