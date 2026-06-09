from sqlalchemy import String, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class UserIdentity(Base, TimestampMixin):
    __tablename__ = "user_identity"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), unique=True, nullable=False, index=True)
    user_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    real_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    id_card_number: Mapped[str | None] = mapped_column(String(18), nullable=True, index=True)
    id_card_front_oss: Mapped[str | None] = mapped_column(String(512))
    id_card_back_oss: Mapped[str | None] = mapped_column(String(512))
    student_card_oss: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    edit_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    verified_at: Mapped[str | None] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    education: Mapped[str | None] = mapped_column(String(32), nullable=True)
    gender: Mapped[str | None] = mapped_column(String(8), nullable=True)
    school: Mapped[str | None] = mapped_column(String(128), nullable=True)
    major: Mapped[str | None] = mapped_column(String(128), nullable=True)
    organization: Mapped[str | None] = mapped_column(String(256), nullable=True)
