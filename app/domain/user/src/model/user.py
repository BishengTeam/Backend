from datetime import datetime

from sqlalchemy import DateTime, String, Integer, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "user"

    openid: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    phone: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # 注销时间：非空表示账号已走注销流程（区别于管理端禁用），后台列表不再展示且不进回收站
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    level2_edit_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    level2_edit_reset_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
