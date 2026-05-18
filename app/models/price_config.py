from sqlalchemy import Boolean, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PriceConfig(Base, TimestampMixin):
    __tablename__ = "price_config"

    cert_type: Mapped[str] = mapped_column(String(64), nullable=False)
    user_type: Mapped[str] = mapped_column(String(16), nullable=False)
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


Index(
    "uq_price_config_active_cert_user",
    PriceConfig.cert_type,
    PriceConfig.user_type,
    unique=True,
    postgresql_where=PriceConfig.is_active.is_(True),
)
