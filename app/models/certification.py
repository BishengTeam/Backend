from sqlalchemy import String, Integer, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Certification(Base, TimestampMixin):
    __tablename__ = "certification"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    vendor: Mapped[str] = mapped_column(String(64), nullable=False)
    price_enterprise: Mapped[int] = mapped_column(Integer, nullable=False)
    price_student: Mapped[int] = mapped_column(Integer, nullable=False)
    requires_xuexin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    pay_first: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
