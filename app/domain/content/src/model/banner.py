from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Banner(Base, TimestampMixin):
    __tablename__ = "banner"

    image_url: Mapped[str] = mapped_column(String(512), nullable=False)
    jump_link: Mapped[str | None] = mapped_column(String(512))
    sort: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
