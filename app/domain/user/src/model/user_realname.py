"""实名信息表 — Level 2: 需审核。首次提交时创建。"""
from sqlalchemy import Integer, String, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class UserRealname(Base, TimestampMixin):
    """身份证实名信息。gender / age / census_register 由身份证号自动计算。"""

    __tablename__ = "user_realname"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), unique=True, nullable=False, index=True)
    user_type: Mapped[str] = mapped_column(String(16), nullable=False)  # student / enterprise
    real_name: Mapped[str] = mapped_column(String(64), nullable=False)  # 保留兼容，由 last_name_zh + first_name_zh 拼接
    last_name_zh: Mapped[str | None] = mapped_column(String(32), nullable=True)   # 姓
    first_name_zh: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 名
    last_name_en: Mapped[str | None] = mapped_column(String(64), nullable=True)   # 拼音姓
    first_name_en: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 拼音名
    id_card_number: Mapped[str] = mapped_column(String(18), nullable=False, index=True)
    id_card_front_oss: Mapped[str | None] = mapped_column(String(512), nullable=True)
    id_card_back_oss: Mapped[str | None] = mapped_column(String(512), nullable=True)
    avatar_oss: Mapped[str | None] = mapped_column(String(512), nullable=True)    # 二寸免冠照片
    # 附属字段（自动计算）
    gender: Mapped[str | None] = mapped_column(String(4), nullable=True)          # 男 / 女
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    birth_date: Mapped[str | None] = mapped_column(String(10), nullable=True)     # YYYY-MM-DD
    census_register: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 户籍（四川省内到市级）
    zip_code: Mapped[str | None] = mapped_column(String(6), nullable=True)          # 邮编（身份证前6位）
    political_status: Mapped[str | None] = mapped_column(String(16), nullable=True) # 政治面貌
    ethnicity: Mapped[str | None] = mapped_column(String(16), nullable=True)        # 民族
    # 审核
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending", index=True)
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 上次审核通过时的字段快照（驳回时恢复）
    verified_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
