"""企业信息表 — Level 2: 需审核。仅 user_type=enterprise 时存在，与 user_student 互斥。"""
from sqlalchemy import Integer, String, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class UserEnterprise(Base, TimestampMixin):
    __tablename__ = "user_enterprise"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), unique=True, nullable=False, index=True)
    organization: Mapped[str] = mapped_column(String(256), nullable=False)
    # 审核
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending", index=True)
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    verified_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
