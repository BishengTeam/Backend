from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    event,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.adapter.database import Base, TimestampMixin

if TYPE_CHECKING:
    from app.domain.order.src.model.order import Order


class Course(Base, TimestampMixin):
    __tablename__ = "course"
    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_course_price_nonnegative"),
        CheckConstraint(
            "status IN ('draft', 'published', 'offline', 'archived')",
            name="ck_course_status",
        ),
        CheckConstraint(
            "preview_chapter_count >= 0",
            name="ck_course_preview_chapter_count_nonnegative",
        ),
        CheckConstraint(
            "((status = 'published' AND is_active = true) OR "
            "(status <> 'published' AND is_active = false))",
            name="ck_course_status_active_consistency",
        ),
        Index("ix_course_status", "status", "id"),
    )

    title: Mapped[str] = mapped_column(String(256), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    cover_storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    teacher_name: Mapped[str | None] = mapped_column(String(64))
    teacher_contact: Mapped[str | None] = mapped_column(String(128))
    preview_chapter_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft", server_default="draft"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    chapters: Mapped[list["CourseChapter"]] = relationship(
        back_populates="course", order_by="CourseChapter.sort_order"
    )
    uploads: Mapped[list["CourseUpload"]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )


class CourseEnrollment(Base, TimestampMixin):
    __tablename__ = "course_enrollment"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_payment', 'enrolled', 'completed', "
            "'refunded', 'cancelled', 'expired')",
            name="ck_course_enrollment_status",
        ),
        CheckConstraint(
            "learning_access IS FALSE OR status IN ('enrolled', 'completed')",
            name="ck_course_enrollment_learning_access",
        ),
        CheckConstraint(
            "(order_id IS NULL AND status <> 'pending_payment') OR order_id IS NOT NULL",
            name="ck_course_enrollment_order_presence",
        ),
        Index("uq_course_enrollment_order_id", "order_id", unique=True),
        Index(
            "uq_course_enrollment_active_user_course",
            "user_id",
            "course_id",
            unique=True,
            postgresql_where=text(
                "status IN ('pending_payment', 'enrolled', 'completed')"
            ),
            sqlite_where=text(
                "status IN ('pending_payment', 'enrolled', 'completed')"
            ),
        ),
        Index("ix_course_enrollment_status", "status"),
    )

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=False, index=True
    )
    course_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("course.id"), nullable=False
    )
    order_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("order.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="pending_payment",
        server_default="pending_payment",
    )
    learning_access: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    access_granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    access_revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    course: Mapped[Course] = relationship()
    order: Mapped[Order | None] = relationship()


class CourseUpload(Base, TimestampMixin):
    """An administrator-owned resumable OSS multipart upload session."""

    __tablename__ = "course_upload"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('cover', 'chapter_video')", name="ck_course_upload_kind"
        ),
        CheckConstraint(
            "status IN ('pending', 'completed', 'bound', 'aborted', 'expired')",
            name="ck_course_upload_status",
        ),
        CheckConstraint("size_bytes > 0", name="ck_course_upload_size_positive"),
        CheckConstraint("part_size > 0", name="ck_course_upload_part_size_positive"),
        CheckConstraint(
            "(kind = 'cover' AND title IS NULL AND duration IS NULL AND sort_order IS NULL) "
            "OR (kind = 'chapter_video' AND title IS NOT NULL "
            "AND duration IS NOT NULL AND sort_order IS NOT NULL)",
            name="ck_course_upload_kind_fields",
        ),
        CheckConstraint(
            "kind = 'cover' OR course_id IS NOT NULL",
            name="ck_course_upload_course_presence",
        ),
        Index("ix_course_upload_course_status", "course_id", "status", "id"),
        Index("ix_course_upload_cleanup", "status", "expires_at"),
    )

    course_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("course.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    oss_upload_id: Mapped[str] = mapped_column(String(256), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(256), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    part_size: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    title: Mapped[str | None] = mapped_column(String(256))
    duration: Mapped[int | None] = mapped_column(Integer)
    sort_order: Mapped[int | None] = mapped_column(Integer)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    course: Mapped[Course | None] = relationship(back_populates="uploads")


class CourseCategory(Base, TimestampMixin):
    __tablename__ = "course_category"
    __table_args__ = (
        CheckConstraint("sort_order >= 0", name="ck_course_category_sort_order"),
        Index("uq_course_category_name", "name", unique=True),
    )

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )


class CourseAuditLog(Base, TimestampMixin):
    """Append-only course-domain audit records."""

    __tablename__ = "course_audit_log"
    __table_args__ = (
        CheckConstraint(
            "result IN ('succeeded', 'failed')", name="ck_course_audit_result"
        ),
        CheckConstraint(
            "actor_type IN ('admin', 'system', 'user')",
            name="ck_course_audit_actor_type",
        ),
        Index("ix_course_audit_object", "object_type", "object_id", "created_at"),
        Index("ix_course_audit_actor", "actor_type", "actor_id", "created_at"),
        Index("ix_course_audit_action", "action", "created_at"),
    )

    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[int | None] = mapped_column(Integer)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    changed_fields: Mapped[dict[str, object] | None] = mapped_column(JSON)
    summary: Mapped[dict[str, object] | None] = mapped_column(JSON)
    request_id: Mapped[str | None] = mapped_column(String(64))
    ip_address: Mapped[str | None] = mapped_column(String(45))


class CourseEntitlementJob(Base, TimestampMixin):
    __tablename__ = "course_entitlement_job"
    __table_args__ = (
        CheckConstraint(
            "action IN ('backfill', 'revoke')",
            name="ck_course_entitlement_job_action",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'partial', 'failed')",
            name="ck_course_entitlement_job_status",
        ),
        CheckConstraint("batch_size > 0", name="ck_course_entitlement_job_batch_size"),
        CheckConstraint(
            "success_count >= 0 AND failure_count >= 0 "
            "AND processed_count >= 0 AND total_count >= 0 "
            "AND success_count + failure_count = processed_count "
            "AND processed_count <= total_count",
            name="ck_course_entitlement_job_counts",
        ),
        CheckConstraint("retry_count >= 0", name="ck_course_entitlement_job_retry_count"),
        Index("ix_course_entitlement_job_queue", "status", "created_at", "id"),
        Index("ix_course_entitlement_job_course", "course_id", "id"),
    )

    course_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("course.id", ondelete="RESTRICT"), nullable=False
    )
    library_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("quiz_library.id", ondelete="RESTRICT"), nullable=False
    )
    binding_id: Mapped[int | None] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="queued", server_default="queued"
    )
    batch_size: Mapped[int] = mapped_column(
        Integer, nullable=False, default=500, server_default="500"
    )
    total_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    processed_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    success_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    failure_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_error: Mapped[str | None] = mapped_column(String(1024))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="SET NULL")
    )


class CourseEntitlementJobItem(Base, TimestampMixin):
    __tablename__ = "course_entitlement_job_item"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed')",
            name="ck_course_entitlement_job_item_status",
        ),
        Index("ix_course_entitlement_job_item_job", "job_id", "status", "id"),
        Index("ix_course_entitlement_job_item_enrollment", "enrollment_id", "id"),
    )

    job_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("course_entitlement_job.id", ondelete="CASCADE"),
        nullable=False,
    )
    enrollment_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("course_enrollment.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    error_message: Mapped[str | None] = mapped_column(String(1024))


@event.listens_for(CourseAuditLog, "before_insert")
def _sanitize_course_audit(_mapper, _connection, target) -> None:
    from app.utils.audit import sanitize_audit_value

    target.changed_fields = sanitize_audit_value(target.changed_fields)
    target.summary = sanitize_audit_value(target.summary)


@event.listens_for(CourseAuditLog, "before_update")
def _reject_course_audit_update(_mapper, _connection, _target) -> None:
    raise ValueError("course audit logs are immutable")


@event.listens_for(CourseAuditLog, "before_delete")
def _reject_course_audit_delete(_mapper, _connection, _target) -> None:
    raise ValueError("course audit logs are immutable")
