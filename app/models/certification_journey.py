from sqlalchemy import ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class CertificationJourneyContent(Base, TimestampMixin):
    __tablename__ = "certification_journey_content"

    scope: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    vendor: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("plan.id", ondelete="CASCADE"))
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[int | None] = mapped_column(Integer)
