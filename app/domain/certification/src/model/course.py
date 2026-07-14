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
    )

    title: Mapped[str] = mapped_column(String(256), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    cover_url: Mapped[str | None] = mapped_column(String(512))
    video_url: Mapped[str | None] = mapped_column(String(512))
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    batches: Mapped[dict | None] = mapped_column(JSON)
    teacher_name: Mapped[str | None] = mapped_column(String(64))
    teacher_contact: Mapped[str | None] = mapped_column(String(128))
    free_preview_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")

    chapters: Mapped[list["CourseChapter"]] = relationship(
        back_populates="course", order_by="CourseChapter.sort_order"
    )
    assets: Mapped[list["CourseAsset"]] = relationship(
        back_populates="course",
        cascade="all, delete-orphan",
        order_by="CourseAsset.sort_order, CourseAsset.id",
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

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    course_id: Mapped[int] = mapped_column(Integer, ForeignKey("course.id"), nullable=False)
    order_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("order.id", ondelete="SET NULL"),
        nullable=True,
    )
    batch_selected: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="pending_payment",
        server_default="pending_payment",
    )
    learning_access: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    access_granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    access_revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    course: Mapped[Course] = relationship()
    order: Mapped[Order | None] = relationship()


class CourseAsset(Base, TimestampMixin):
    __tablename__ = "course_asset"
    __table_args__ = (
        CheckConstraint("sort_order >= 0", name="ck_course_asset_sort_order_nonnegative"),
        Index("ix_course_asset_course_sort", "course_id", "sort_order", "id"),
    )

    course_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("course.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_preview: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    course: Mapped[Course] = relationship(back_populates="assets")
