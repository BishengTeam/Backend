"""考场计划配套附件（考试须知、考场指引等）。"""
from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class CertAttachment(Base, TimestampMixin):
    """按厂商/考场计划挂载的公共附件，四个厂商共用。"""
    __tablename__ = "cert_attachment"
    __table_args__ = (
        Index("ix_cert_attachment_vendor_plan", "vendor", "plan_id", "is_active"),
        Index(
            "ix_cert_attachment_vendor", "vendor", "schedule_id", "is_active"
        ),
        CheckConstraint(
            "plan_id IS NULL OR schedule_id IS NULL",
            name="ck_cert_attachment_scope",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    vendor: Mapped[str] = mapped_column(String(32), nullable=False)
    plan_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("plan.id", ondelete="CASCADE"),
        nullable=True,
    )
    schedule_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("exam_schedule.id", ondelete="CASCADE"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    file_url: Mapped[str] = mapped_column(String(512), nullable=False)
    file_size: Mapped[int | None] = mapped_column(Integer)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
