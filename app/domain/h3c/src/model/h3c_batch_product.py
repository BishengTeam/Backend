"""H3C 批次×产品关联表（纯关联，无价格）。"""
from sqlalchemy import ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class H3cBatchProduct(Base, TimestampMixin):
    """批次包含的科目。价格在 cert_product 上维护。"""
    __tablename__ = "h3c_batch_product"
    __table_args__ = (
        UniqueConstraint("batch_id", "product_id", name="uq_h3c_batch_product"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("h3c_exam_batch.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("cert_product.id", ondelete="RESTRICT"), nullable=False
    )
