from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class Banner(Base, TimestampMixin):
    __tablename__ = "banner"

    image_url: Mapped[str] = mapped_column(String(512), nullable=False)
    jump_link: Mapped[str | None] = mapped_column(String(512))
    target_type: Mapped[str | None] = mapped_column(
        String(32), comment="跳转资源类型: cert/course/activity/zone/url"
    )
    target_id: Mapped[int | None] = mapped_column(
        Integer, comment="跳转资源 ID，target_type=url 时为空"
    )
    sort: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
