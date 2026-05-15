from sqlalchemy import String, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Order(Base, TimestampMixin):
    __tablename__ = "order"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    cert_type: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_name: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_phone: Mapped[str] = mapped_column(String(20), nullable=False)
    candidate_idcard: Mapped[str | None] = mapped_column(String(20))
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending", index=True)
    out_trade_no: Mapped[str | None] = mapped_column(String(64), unique=True)
    transaction_id: Mapped[str | None] = mapped_column(String(64))
    paid_at: Mapped[str | None] = mapped_column(String(30))
