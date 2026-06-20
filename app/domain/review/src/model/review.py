from sqlalchemy import String, Integer, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class Review(Base, TimestampMixin):
    __tablename__ = "review"

    target_type: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True,
        comment="identity | student | enterprise | order"
    )
    target_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    reviewer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("admin_user.id"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="approve | reject"
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
