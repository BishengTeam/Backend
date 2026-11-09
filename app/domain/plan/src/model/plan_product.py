"""批次×产品关联：容量与人社专属字段存于此级。

价格不存于此：H3C/NISP 从厂商扩展表取价，其余从 PriceConfig 取价。
"""
from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import BigInteger as _BigInteger, Integer as _Integer

from app.adapter.database import Base, TimestampMixin

# SQLite 测试兼容：SQLite 需要 Integer 自增，PostgreSQL 用 BigInteger
_ID = _BigInteger().with_variant(_Integer(), "sqlite")


class PlanProduct(Base, TimestampMixin):
    __tablename__ = "plan_product"
    __table_args__ = (
        UniqueConstraint("plan_id", "product_id", name="uq_plan_product"),
        CheckConstraint("capacity >= 0", name="ck_plan_product_capacity"),
        Index("ix_plan_product_product", "product_id"),
        Index("ix_plan_product_plan", "plan_id"),
    )

    id: Mapped[int] = mapped_column(_ID, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("plan.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("cert_product.id", ondelete="RESTRICT"), nullable=False
    )
    capacity: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # 人社专属（product.type='renshe' 时应用层校验必填）
    renshe_config_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("renshe_project_config.id", ondelete="RESTRICT"), nullable=True
    )
    renshe_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    occupation_name: Mapped[str | None] = mapped_column(String(64))
    occupation_code: Mapped[str | None] = mapped_column(String(160))
    skill_level: Mapped[str | None] = mapped_column(String(32))
    exam_type: Mapped[str | None] = mapped_column(String(16))
    application_type: Mapped[str | None] = mapped_column(String(16))
