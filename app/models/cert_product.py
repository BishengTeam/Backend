from sqlalchemy import Boolean, Integer, String
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class CertProduct(Base, TimestampMixin):
    __tablename__ = "cert_product"

    type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    catalog_id: Mapped[int | None] = mapped_column(
        ForeignKey("cert_product_catalog.id", ondelete="SET NULL"), nullable=True
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    chinese_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(512))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
