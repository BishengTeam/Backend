from datetime import datetime

from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Coupon(Base, TimestampMixin):
    __tablename__ = "coupon"

    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    type: Mapped[str] = mapped_column(String(16), default="fixed", server_default="fixed")
    value: Mapped[int] = mapped_column(Integer, nullable=False)
    min_order_amount: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class UserCoupon(Base, TimestampMixin):
    __tablename__ = "user_coupon"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    coupon_id: Mapped[int] = mapped_column(Integer, ForeignKey("coupon.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="unused", server_default="unused")
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
