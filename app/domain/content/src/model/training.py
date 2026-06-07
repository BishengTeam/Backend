from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class Training(Base, TimestampMixin):
    __tablename__ = "training"

    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    cover_url: Mapped[str | None] = mapped_column(String(512))
    location: Mapped[str | None] = mapped_column(String(256))
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_participants: Mapped[int] = mapped_column(Integer, default=0)
    cert_type: Mapped[str | None] = mapped_column(String(64), comment="关联认证类型")
    price: Mapped[int] = mapped_column(Integer, default=0, comment="培训费用（分）")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
