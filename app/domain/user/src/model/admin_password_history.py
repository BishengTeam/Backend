"""Administrator password history persistence model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, event, func
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base


class AdminPasswordHistory(Base):
    """An immutable password hash retained for recent-password comparison.

    The authentication service may delete rows older than the five retained
    hashes, but an existing history row must never be rewritten.
    """

    __tablename__ = "admin_password_history"
    __table_args__ = (
        Index(
            "ix_admin_password_history_recent",
            "admin_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("admin_user.id", ondelete="RESTRICT"),
        nullable=False,
    )
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


@event.listens_for(AdminPasswordHistory, "before_update")
def _reject_admin_password_history_update(_mapper, _connection, _target) -> None:
    raise ValueError("administrator password history is immutable")


__all__ = ["AdminPasswordHistory"]
