from sqlalchemy import Integer, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class Collection(Base, TimestampMixin):
    __tablename__ = "collection"

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=False, index=True
    )
    target_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="收藏类型: course / material"
    )
    target_id: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="目标资源 ID"
    )
