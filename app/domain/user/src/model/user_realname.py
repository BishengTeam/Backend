"""实名信息表 — Level 2: 需审核。首次提交时创建。"""
from sqlalchemy import Integer, String, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class UserRealname(Base, TimestampMixin):
    """身份证实名信息。gender / age / census_register 由身份证号自动计算。"""

    __tablename__ = "user_realname"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), unique=True, nullable=False, index=True)
    user_type: Mapped[str] = mapped_column(String(16), nullable=False)  # student / enterprise
    real_name: Mapped[str] = mapped_column(String(64), nullable=False)
    id_card_number: Mapped[str] = mapped_column(String(18), nullable=False, index=True)
    id_card_front_oss: Mapped[str | None] = mapped_column(String(512), nullable=True)
    id_card_back_oss: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # 附属字段（自动计算）
    gender: Mapped[str | None] = mapped_column(String(4), nullable=True)          # 男 / 女
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    census_register: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 户籍（四川省内到市级）
    # 审核
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending", index=True)
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 上次审核通过时的字段快照（驳回时恢复）
    verified_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
