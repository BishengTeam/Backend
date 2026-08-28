from sqlalchemy import Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class CertProductCatalog(Base, TimestampMixin):
    """认证产品目录（厂商价格表导入的受控候选清单）。

    ``code`` 存厂商官方考试代码（如 GB0-192），``name`` 存展示名称
    （如 "H3CNE-RS+ H3C认证路由交换网络工程师"）。认证管理员只能从
    目录中选取编码创建产品；超级管理员可解锁自由输入。
    """

    __tablename__ = "cert_product_catalog"
    __table_args__ = (
        UniqueConstraint("type", "code", name="uq_cert_catalog_type_code"),
    )

    type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    question_count: Mapped[int | None] = mapped_column(Integer)
    total_score: Mapped[int | None] = mapped_column(Integer)
    pass_score: Mapped[int | None] = mapped_column(Integer)
    cert_validity_years: Mapped[int | None] = mapped_column(Integer)
    retake_count: Mapped[int | None] = mapped_column(Integer)
    prerequisite: Mapped[str | None] = mapped_column(Text)
    remark: Mapped[str | None] = mapped_column(String(256))
    source: Mapped[str | None] = mapped_column(String(128))
