from datetime import datetime

from sqlalchemy import DateTime, String, Integer, Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class Zone(Base, TimestampMixin):
    __tablename__ = "zone"

    zone_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    cover_url: Mapped[str | None] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text)
    link_url: Mapped[str | None] = mapped_column(String(512))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    is_banner: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
