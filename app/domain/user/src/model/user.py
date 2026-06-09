from sqlalchemy import String, Integer, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "user"

    openid: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    phone: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    level2_edit_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    level2_edit_reset_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
