from sqlalchemy import String, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class UserIdentity(Base, TimestampMixin):
    __tablename__ = "user_identity"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), unique=True, nullable=False, index=True)
    real_name: Mapped[str] = mapped_column(String(64), nullable=False)
    id_card_number: Mapped[str] = mapped_column(String(18), nullable=False, index=True)
    id_card_front_oss: Mapped[str | None] = mapped_column(String(512))
    id_card_back_oss: Mapped[str | None] = mapped_column(String(512))
    student_card_oss: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(16), default="verified", server_default="verified", index=True)
    edit_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    verified_at: Mapped[str | None] = mapped_column(String(30))
