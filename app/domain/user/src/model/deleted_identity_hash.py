"""Permanent, irreversible identity audit left after account deletion."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class DeletedIdentityHash(Base, TimestampMixin):
    __tablename__ = "deleted_identity_hash"

    former_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    id_card_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    released_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
