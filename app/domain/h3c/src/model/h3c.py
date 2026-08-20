from __future__ import annotations

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


H3C_REGISTRATION_TYPES = ("coupon", "student", "full")
H3C_MATERIAL_TYPES = ("coupon_proof", "student_proof")
H3C_REJECTION_REASONS = (
    "image_unclear",
    "image_incomplete",
    "material_type_mismatch",
    "verify_code_invalid",
    "suspected_forged_material",
)


class H3cExamBatch(Base, TimestampMixin):
    """H3C-specific immutable configuration attached to a generic Plan."""

    __tablename__ = "h3c_exam_batch"
    __table_args__ = (
        CheckConstraint(
            "coupon_price_cents >= 0 AND student_price_cents >= 0 "
            "AND full_price_cents >= 0",
            name="ck_h3c_batch_prices_nonnegative",
        ),
        CheckConstraint(
            "payment_timeout_minutes BETWEEN 1 AND 1440",
            name="ck_h3c_payment_timeout",
        ),
        CheckConstraint(
            "resubmission_window_hours BETWEEN 1 AND 720",
            name="ck_h3c_resubmission_window",
        ),
        CheckConstraint(
            "max_resubmissions BETWEEN 0 AND 10",
            name="ck_h3c_max_resubmissions",
        ),
        CheckConstraint(
            "max_material_bytes BETWEEN 1 AND 20971520",
            name="ck_h3c_material_size",
        ),
        UniqueConstraint("plan_id", name="uq_h3c_batch_plan"),
    )

    plan_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("plan.id", ondelete="RESTRICT"), nullable=False
    )
    exam_code: Mapped[str] = mapped_column(String(64), nullable=False)
    country: Mapped[str] = mapped_column(String(8), nullable=False, default="CHN")
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="CHS")
    identity_tag: Mapped[str] = mapped_column(String(64), nullable=False)
    training_org: Mapped[str | None] = mapped_column(String(128))
    training_teacher: Mapped[str | None] = mapped_column(String(64))
    training_address: Mapped[str | None] = mapped_column(String(256))
    training_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    training_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    coupon_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    student_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    full_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
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


class H3cRegistration(Base, TimestampMixin):
    __tablename__ = "h3c_registration"
    __table_args__ = (
        CheckConstraint(
            "registration_type IN ('coupon', 'student', 'full')",
            name="ck_h3c_registration_type",
        ),
        CheckConstraint(
            "status IN ('pending_payment', 'pending_review', "
            "'rejected_awaiting_resubmission', 'pending_refund_confirmation', "
            "'refund_processing', 'approved', 'refunded_closed', 'cancelled')",
            name="ck_h3c_registration_status",
        ),
        CheckConstraint(
            "resubmission_count >= 0", name="ck_h3c_resubmission_count"
        ),
        CheckConstraint("rejection_count >= 0", name="ck_h3c_rejection_count"),
        UniqueConstraint("registration_no", name="uq_h3c_registration_no"),
        UniqueConstraint("order_id", name="uq_h3c_registration_order"),
        Index("ix_h3c_registration_batch_status", "batch_id", "status"),
        Index(
            "uq_h3c_registration_active_batch_idcard",
            "batch_id",
            "candidate_idcard",
            unique=True,
            postgresql_where=text(
                "status IN ('pending_payment', 'pending_review', "
                "'rejected_awaiting_resubmission', 'pending_refund_confirmation', "
                "'refund_processing', 'approved')"
            ),
            sqlite_where=text(
                "status IN ('pending_payment', 'pending_review', "
                "'rejected_awaiting_resubmission', 'pending_refund_confirmation', "
                "'refund_processing', 'approved')"
            ),
        ),
        Index(
            "ix_h3c_registration_resubmission_due",
            "status",
            "resubmission_due_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("h3c_exam_batch.id", ondelete="RESTRICT"), nullable=False
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
    registration_type: Mapped[str] = mapped_column(
        String(16), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    candidate_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    candidate_idcard: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True
    )
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
    close_reason: Mapped[str | None] = mapped_column(String(64))


class H3cMaterial(Base, TimestampMixin):
    __tablename__ = "h3c_material"
    __table_args__ = (
        CheckConstraint(
            "material_type IN ('coupon_proof', 'student_proof')",
            name="ck_h3c_material_type",
        ),
        CheckConstraint(
            "version_no > 0", name="ck_h3c_material_version_positive"
        ),
        CheckConstraint(
            "size_bytes > 0", name="ck_h3c_material_size_positive"
        ),
        UniqueConstraint(
            "registration_id",
            "material_type",
            "version_no",
            name="uq_h3c_material_version",
        ),
        Index("ix_h3c_material_current", "registration_id", "is_current"),
    )

    registration_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("h3c_registration.id", ondelete="RESTRICT"), nullable=False
    )
    material_type: Mapped[str] = mapped_column(String(24), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(
        String(64), nullable=False, default="image/jpeg"
    )
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    is_current: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class H3cMaterialUpload(Base, TimestampMixin):
    """Pre-registration upload receipt used to bind an owned key to its bytes."""

    __tablename__ = "h3c_material_upload"
    __table_args__ = (
        CheckConstraint(
            "material_type IN ('coupon_proof', 'student_proof')",
            name="ck_h3c_material_upload_type",
        ),
        CheckConstraint("size_bytes > 0", name="ck_h3c_material_upload_size"),
        UniqueConstraint("storage_key", name="uq_h3c_material_upload_key"),
        Index("ix_h3c_material_upload_user", "user_id", "created_at"),
    )

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False
    )
    material_type: Mapped[str] = mapped_column(String(24), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class H3cReview(Base, TimestampMixin):
    __tablename__ = "h3c_review"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('approved', 'rejected')",
            name="ck_h3c_review_decision",
        ),
        CheckConstraint(
            "decision <> 'rejected' OR "
            "(reason_code IS NOT NULL AND length(trim(reason_code)) > 0)",
            name="ck_h3c_review_rejection_reason",
        ),
        Index("ix_h3c_review_registration", "registration_id", "id"),
    )

    registration_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("h3c_registration.id", ondelete="RESTRICT"), nullable=False
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64))
    reason_detail: Mapped[str | None] = mapped_column(Text)
    rejected_material_types: Mapped[list | None] = mapped_column(JSONB)
    material_version_ids: Mapped[list | None] = mapped_column(JSONB)
    reviewer_admin_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="RESTRICT"), nullable=False
    )
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class H3cRefundRequest(Base, TimestampMixin):
    __tablename__ = "h3c_refund_request"
    __table_args__ = (
        CheckConstraint(
            "request_kind IN ('review_failed', 'batch_cancelled', 'exception_close')",
            name="ck_h3c_refund_kind",
        ),
        CheckConstraint(
            "status IN ('requested', 'approved', 'processing', 'succeeded', 'failed')",
            name="ck_h3c_refund_status",
        ),
        CheckConstraint(
            "amount_cents >= 0", name="ck_h3c_refund_amount_nonnegative"
        ),
        Index("ix_h3c_refund_status", "status", "id"),
        Index(
            "uq_h3c_refund_active_order",
            "order_id",
            unique=True,
            postgresql_where=text(
                "status IN ('requested', 'approved', 'processing', 'failed')"
            ),
            sqlite_where=text(
                "status IN ('requested', 'approved', 'processing', 'failed')"
            ),
        ),
    )

    registration_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("h3c_registration.id", ondelete="RESTRICT"), nullable=False
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
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="requested"
    )
    requested_by_admin_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("admin_user.id")
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    approved_by_admin_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("admin_user.id")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    out_refund_no: Mapped[str | None] = mapped_column(String(64), unique=True)
    wechat_refund_id: Mapped[str | None] = mapped_column(
        String(64), unique=True
    )
    processing_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    succeeded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )


class H3cExportJob(Base, TimestampMixin):
    __tablename__ = "h3c_export_job"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_h3c_export_job_status",
        ),
        CheckConstraint(
            "artifact_type IN ('embedded_xlsx', 'images_zip')",
            name="ck_h3c_export_artifact_type",
        ),
        CheckConstraint(
            "registration_count >= 0", name="ck_h3c_export_count_nonnegative"
        ),
        Index("ix_h3c_export_status", "status", "id"),
        Index("ix_h3c_export_expires", "status", "expires_at"),
    )

    batch_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("h3c_exam_batch.id", ondelete="RESTRICT"), nullable=False
    )
    registration_type: Mapped[str] = mapped_column(String(16), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(16), nullable=False)
    requested_by_admin_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="RESTRICT"), nullable=False
    )
    include_statuses: Mapped[list] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="queued"
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


class H3cExportItem(Base, TimestampMixin):
    __tablename__ = "h3c_export_item"
    __table_args__ = (
        UniqueConstraint("job_id", "registration_id", name="uq_h3c_export_item"),
        Index("ix_h3c_export_item_registration", "registration_id", "id"),
    )

    job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("h3c_export_job.id", ondelete="RESTRICT"), nullable=False
    )
    registration_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("h3c_registration.id", ondelete="RESTRICT"), nullable=False
    )
