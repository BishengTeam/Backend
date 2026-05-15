from sqlalchemy import String, Integer, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "user"

    openid: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    phone: Mapped[str | None] = mapped_column(String(20))
    user_type: Mapped[str] = mapped_column(String(16), default="student", server_default="student")
    age_group: Mapped[str | None] = mapped_column(String(16))
    certification_interest: Mapped[str | None] = mapped_column(String(64))
    profile_edit_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
