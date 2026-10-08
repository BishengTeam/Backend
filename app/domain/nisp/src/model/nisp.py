"""NISP certification domain models.

Mirrors the H3C domain pattern for offline computer-based exams.
Batch configuration attaches to a generic Plan; registrations snapshot
the candidate data required by the NISP Excel export template.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


NISP_LEVELS = ("1", "2")

NISP_MATERIAL_TYPES = (
    "id_card_both_sides",   # 身份证双面 (all levels)
    "portrait_photo",       # 寸照 (all levels)
    "xuexin_report",        # 学籍报告 (level 2 only)
    "application_form",     # NISP二级申请表模板 (level 2 only)
)

NISP_REGISTRATION_STATUSES = (
    "pending_payment",
    "pending_review",
    "rejected_awaiting_resubmission",
    "pending_refund_confirmation",
    "refund_processing",
    "approved",
    "refunded_closed",
    "cancelled",
)

NISP_REJECTION_REASONS = (
    "image_unclear",
    "image_incomplete",
    "material_type_mismatch",
    "id_number_mismatch",
    "suspected_forged_material",
    "application_form_incomplete",
)


class NispExamBatch(Base, TimestampMixin):
    """NISP-specific configuration attached to a generic Plan (offline exam)."""

    __tablename__ = "nisp_exam_batch"
    __table_args__ = (
        CheckConstraint(
            "level IN ('1', '2')",
            name="ck_nisp_batch_level",
        ),
        CheckConstraint(
            "level1_price_cents >= 0 AND level2_price_cents >= 0",
            name="ck_nisp_batch_prices_nonnegative",
        ),
        CheckConstraint(
            "payment_timeout_minutes BETWEEN 1 AND 1440",
            name="ck_nisp_payment_timeout",
        ),
        CheckConstraint(
            "resubmission_window_hours BETWEEN 1 AND 720",
            name="ck_nisp_resubmission_window",
        ),
        CheckConstraint(
            "max_resubmissions BETWEEN 0 AND 10",
            name="ck_nisp_max_resubmissions",
        ),
        CheckConstraint(
            "max_material_bytes BETWEEN 1 AND 20971520",
            name="ck_nisp_max_material_size",
        ),
        UniqueConstraint("plan_id", name="uq_nisp_batch_plan"),
    )

    plan_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("plan.id", ondelete="RESTRICT"), nullable=False
    )
    level: Mapped[str] = mapped_column(String(4), nullable=False, default="1")
    training_org: Mapped[str | None] = mapped_column(String(128))
    training_teacher: Mapped[str | None] = mapped_column(String(64))
    training_address: Mapped[str | None] = mapped_column(String(256))
    training_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    training_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    level1_price_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    level2_price_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payment_timeout_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30
    )
    resubmission_window_hours: Mapped[int] = mapped_column(
        Integer, nullable=False, default=72
    )
    max_resubmissions: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2
    )
    max_material_bytes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10 * 1024 * 1024
    )


class NispRegistration(Base, TimestampMixin):
    """A user's NISP registration for a specific batch and level."""

    __tablename__ = "nisp_registration"
    __table_args__ = (
        CheckConstraint(
            "level IN ('1', '2')",
            name="ck_nisp_registration_level",
        ),
        CheckConstraint(
            "status IN ('pending_payment', 'pending_review', "
            "'rejected_awaiting_resubmission', 'pending_refund_confirmation', "
            "'refund_processing', 'approved', 'refunded_closed', 'cancelled')",
            name="ck_nisp_registration_status",
        ),
        CheckConstraint("resubmission_count >= 0", name="ck_nisp_resub_count"),
        CheckConstraint("rejection_count >= 0", name="ck_nisp_rejection_count"),
        UniqueConstraint("registration_no", name="uq_nisp_registration_no"),
        UniqueConstraint("order_id", name="uq_nisp_registration_order"),
        Index(
            "uq_nisp_registration_active_batch_idcard",
            "batch_id",
            "candidate_idcard",
            unique=True,
            postgresql_where=text(
                "status IN ('pending_payment', 'pending_review', "
                "'rejected_awaiting_resubmission', 'pending_refund_confirmation', "
                "'refund_processing', 'approved')"
            ),
        ),
        Index("ix_nisp_registration_batch_status", "batch_id", "status"),
        Index(
            "ix_nisp_registration_resubmission_due",
            "status",
            "resubmission_due_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("nisp_exam_batch.id", ondelete="RESTRICT"), nullable=False
    )
    plan_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("plan.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("order.id", ondelete="RESTRICT"), nullable=False
    )
    registration_no: Mapped[str] = mapped_column(String(40), nullable=False)
    level: Mapped[str] = mapped_column(String(4), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    # Snapshot matching NISP Excel export columns
    candidate_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    candidate_idcard: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True
    )

    # Attached material storage keys (JSON list)
    material_keys: Mapped[dict | None] = mapped_column(JSONB)

    resubmission_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    rejection_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resubmission_due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[str | None] = mapped_column(String(128))


class NispMaterialFile(Base, TimestampMixin):
    """A private OSS object and its stable material metadata.

    A row is created immediately after upload and bound to one registration when
    the candidate submits the order. Rejected candidates can replace a current
    material; the previous row remains addressable for audit purposes.
    """

    __tablename__ = "nisp_material_file"
    __table_args__ = (
        CheckConstraint(
            "material_type IN ('id_card_both_sides', 'portrait_photo', "
            "'xuexin_report', 'application_form')",
            name="ck_nisp_material_file_type",
        ),
        CheckConstraint("length(storage_key) > 0", name="ck_nisp_material_key_nonempty"),
        CheckConstraint(
            "registration_id IS NULL OR is_current = false OR bound_at IS NOT NULL",
            name="ck_nisp_material_bound_current",
        ),
        Index(
            "uq_nisp_material_current_registration_type",
            "registration_id",
            "material_type",
            unique=True,
            postgresql_where=text("is_current AND registration_id IS NOT NULL"),
            sqlite_where=text("is_current AND registration_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    registration_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("nisp_registration.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    material_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    original_filename: Mapped[str | None] = mapped_column(String(256))
    version_no: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64))
    is_current: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    bound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NispReview(Base, TimestampMixin):
    """Immutable review decision for a NISP registration."""

    __tablename__ = "nisp_review"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('approved', 'rejected')",
            name="ck_nisp_review_decision",
        ),
        Index("ix_nisp_review_registration", "registration_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    registration_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("nisp_registration.id", ondelete="CASCADE"),
        nullable=False,
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64))
    reason_detail: Mapped[str | None] = mapped_column(Text)
    rejected_material_types: Mapped[list | None] = mapped_column(JSONB)
    material_version_ids: Mapped[list | None] = mapped_column(JSONB)
    reviewer_admin_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="RESTRICT"), nullable=False
    )
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NispRefundRequest(Base, TimestampMixin):
    """Refund request for a NISP registration."""

    __tablename__ = "nisp_refund_request"
    __table_args__ = (
        CheckConstraint(
            "request_kind IN ('review_failed', 'batch_cancelled', 'exception_close')",
            name="ck_nisp_refund_kind",
        ),
        CheckConstraint(
            "status IN ('requested', 'approved', 'processing', 'succeeded', 'failed')",
            name="ck_nisp_refund_status",
        ),
        CheckConstraint(
            "amount_cents >= 0", name="ck_nisp_refund_amount_nonnegative"
        ),
        Index("ix_nisp_refund_status", "status", "id"),
        Index(
            "uq_nisp_refund_active_order",
            "order_id",
            unique=True,
            postgresql_where=text(
                "status IN ('requested', 'approved', 'processing', 'failed')"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    registration_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("nisp_registration.id", ondelete="RESTRICT"),
        nullable=False,
    )
    order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("order.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False
    )
    request_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_detail: Mapped[str | None] = mapped_column(Text)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="requested")
    requested_by_admin_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("admin_user.id")
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approved_by_admin_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("admin_user.id")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    out_refund_no: Mapped[str | None] = mapped_column(String(64), unique=True)
    wechat_refund_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    processing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class NispExportJob(Base, TimestampMixin):
    """Async export job for a NISP registration data package."""

    __tablename__ = "nisp_export_job"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_nisp_export_job_status",
        ),
        CheckConstraint(
            "level IN ('1', '2')",
            name="ck_nisp_export_level",
        ),
        CheckConstraint(
            "registration_count >= 0",
            name="ck_nisp_export_count_nonnegative",
        ),
        Index("ix_nisp_export_status", "status", "id"),
        Index("ix_nisp_export_expires", "status", "expires_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("nisp_exam_batch.id", ondelete="RESTRICT"), nullable=False
    )
    level: Mapped[str] = mapped_column(String(4), nullable=False)
    requested_by_admin_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="RESTRICT"), nullable=False
    )
    include_statuses: Mapped[list] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="queued"
    )
    artifact_type: Mapped[str] = mapped_column(
        String(24), nullable=False, default="full_package", server_default="full_package"
    )
    registration_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    storage_key: Mapped[str | None] = mapped_column(String(512))
    artifact_sha256: Mapped[str | None] = mapped_column(String(64))
    artifact_bytes: Mapped[int | None] = mapped_column(Integer)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    result_summary: Mapped[dict | None] = mapped_column(JSONB)
