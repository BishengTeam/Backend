"""Immutable, product-owned admission configuration versions."""

from sqlalchemy import ForeignKey, Integer, JSON, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class RensheProjectConfig(Base, TimestampMixin):
    __tablename__ = "renshe_project_config"
    __table_args__ = (
        UniqueConstraint("product_id", "version", name="uq_renshe_project_version"),
    )

    product_id: Mapped[int] = mapped_column(
        ForeignKey("cert_product.id", ondelete="RESTRICT"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False)


@event.listens_for(RensheProjectConfig, "before_update")
def forbid_configuration_rewrite(mapper, connection, target):
    raise ValueError("项目配置版本不可修改，请创建新版本")
