from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Inventory(Base, TimestampMixin):
    __tablename__ = "inventory"
    __table_args__ = (
        UniqueConstraint("inventory_type", "ref_code", name="uq_inventory_type_ref_code"),
        CheckConstraint("total_quota >= 0", name="ck_inventory_total_quota_non_negative"),
        CheckConstraint("available_quota >= 0", name="ck_inventory_available_quota_non_negative"),
        CheckConstraint("locked_quota >= 0", name="ck_inventory_locked_quota_non_negative"),
        CheckConstraint("sold_quota >= 0", name="ck_inventory_sold_quota_non_negative"),
        CheckConstraint(
            "available_quota + locked_quota + sold_quota = total_quota",
            name="ck_inventory_quota_balance",
        ),
    )

    inventory_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="certification", server_default="certification", index=True
    )
    ref_code: Mapped[str] = mapped_column(String(64), nullable=False)
    total_quota: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    available_quota: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    locked_quota: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    sold_quota: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")


class InventoryRecord(Base, TimestampMixin):
    __tablename__ = "inventory_record"

    inventory_id: Mapped[int] = mapped_column(Integer, ForeignKey("inventory.id"), nullable=False, index=True)
    order_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("order.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    before_total_quota: Mapped[int] = mapped_column(Integer, nullable=False)
    before_available_quota: Mapped[int] = mapped_column(Integer, nullable=False)
    before_locked_quota: Mapped[int] = mapped_column(Integer, nullable=False)
    before_sold_quota: Mapped[int] = mapped_column(Integer, nullable=False)
    after_total_quota: Mapped[int] = mapped_column(Integer, nullable=False)
    after_available_quota: Mapped[int] = mapped_column(Integer, nullable=False)
    after_locked_quota: Mapped[int] = mapped_column(Integer, nullable=False)
    after_sold_quota: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(256))
