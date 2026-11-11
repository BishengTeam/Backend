"""全局假期表 — 所有认证共用。"""
from datetime import date

from sqlalchemy import Boolean, Date, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class Holiday(Base, TimestampMixin):
    __tablename__ = "holiday"
    __table_args__ = (UniqueConstraint("holiday_date", name="uq_holiday_date"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_annual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
