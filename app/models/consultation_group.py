from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class CertConsultationGroup(Base, TimestampMixin):
    """Current consultation-group configuration for one certification batch."""

    __tablename__ = "cert_consultation_group"
    __table_args__ = (UniqueConstraint("plan_id", name="uq_cert_consultation_group_plan"),)

    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    qrcode_url: Mapped[str] = mapped_column(String(512), nullable=False)
    join_instructions: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[int | None] = mapped_column(Integer)
