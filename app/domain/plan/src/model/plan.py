"""认证批次重构 v2：Plan 从"单产品计划"变为"跨厂商批次"。

批次本身不存储产品信息、容量、价格；这些全部下放到 plan_product。
价格由 PriceConfig（通用产品）或厂商扩展表（H3C/NISP）提供。
"""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class Plan(Base, TimestampMixin):
    """认证批次（跨厂商）。"""

    __tablename__ = "plan"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'published', 'registration_closed', "
            "'finalized', 'archived', 'cancelled')",
            name="ck_plan_status",
        ),
        CheckConstraint("sort_order >= 0", name="ck_plan_sort_order_nonnegative"),
        Index("uq_plan_name", "name", unique=True),
        Index("ix_plan_status", "status"),
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    apply_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    apply_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exam_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exam_location: Mapped[str | None] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text)
    contact_name: Mapped[str | None] = mapped_column(String(64))
    contact_phone: Mapped[str | None] = mapped_column(String(20))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    registration_closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleanup_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="draft", server_default="draft"
    )

# ============================================================
# v2 兼容层：通过 relationship 提供旧字段访问
# 前提：plan 从 DB 加载后 products 已通过 lazy="selectin" 填充
# ============================================================

from sqlalchemy.orm import relationship

Plan.products = relationship(
    "PlanProduct",
    foreign_keys="PlanProduct.plan_id",
    cascade="all, delete-orphan",
    lazy="selectin",
    order_by="PlanProduct.id",
)

Plan.time_segments_rel = relationship(
    "PlanTimeSegment",
    foreign_keys="PlanTimeSegment.plan_id",
    cascade="all, delete-orphan",
    lazy="noload",
    order_by="PlanTimeSegment.sort_order",
)


def _first_pp(plan):
    products = getattr(plan, "products", None)
    if products:
        return products[0]
    return None


@property
def _compat_product_type(self) -> str:
    """v2 兼容：返回第一个关联产品的编码。

    注意：PlanProduct 不直接存 code，此属性仅在测试/迁移兼容层使用。
    生产代码应 join PlanProduct→CertProduct 查询。
    """
    pp = _first_pp(self)
    if pp is None:
        return ""
    # 尝试从 relationship 获取（需要 cert_product_rel）
    cert = getattr(pp, "cert_product", None)
    if cert is not None:
        return cert.code
    return ""


@_compat_product_type.setter
def _compat_product_type(self, value: str) -> None:
    pp = _first_pp(self)
    if pp is not None:
        pp._product_type_override = value


@property
def _compat_capacity(self) -> int:
    pp = _first_pp(self)
    return pp.capacity if pp else 0


@_compat_capacity.setter
def _compat_capacity(self, value: int) -> None:
    pp = _first_pp(self)
    if pp is not None:
        pp.capacity = value


@property
def _compat_price_cents(self) -> int:
    return 0  # v2: 批次不存价格


@property
def _compat_skill_level(self) -> str | None:
    pp = _first_pp(self)
    return pp.skill_level if pp else None


@_compat_skill_level.setter
def _compat_skill_level(self, value) -> None:
    pp = _first_pp(self)
    if pp is not None:
        pp.skill_level = value


@property
def _compat_occupation_name(self) -> str | None:
    pp = _first_pp(self)
    return pp.occupation_name if pp else None


@_compat_occupation_name.setter
def _compat_occupation_name(self, value) -> None:
    pp = _first_pp(self)
    if pp is not None:
        pp.occupation_name = value


@property
def _compat_occupation_code(self) -> str | None:
    pp = _first_pp(self)
    return pp.occupation_code if pp else None


@_compat_occupation_code.setter
def _compat_occupation_code(self, value) -> None:
    pp = _first_pp(self)
    if pp is not None:
        pp.occupation_code = value


@property
def _compat_exam_type(self) -> str | None:
    pp = _first_pp(self)
    return pp.exam_type if pp else None


@_compat_exam_type.setter
def _compat_exam_type(self, value) -> None:
    pp = _first_pp(self)
    if pp is not None:
        pp.exam_type = value


@property
def _compat_application_type(self) -> str | None:
    pp = _first_pp(self)
    return pp.application_type if pp else None


@_compat_application_type.setter
def _compat_application_type(self, value) -> None:
    pp = _first_pp(self)
    if pp is not None:
        pp.application_type = value


@property
def _compat_renshe_config(self) -> dict | None:
    pp = _first_pp(self)
    return pp.renshe_config if pp else None


@_compat_renshe_config.setter
def _compat_renshe_config(self, value: dict | None) -> None:
    pp = _first_pp(self)
    if pp is not None:
        pp.renshe_config = value


@property
def _compat_renshe_config_id(self) -> int | None:
    pp = _first_pp(self)
    return pp.renshe_config_id if pp else None


@_compat_renshe_config_id.setter
def _compat_renshe_config_id(self, value: int | None) -> None:
    pp = _first_pp(self)
    if pp is not None:
        pp.renshe_config_id = value


@property
def _compat_project_name(self) -> str | None:
    return None  # v2: 由产品名替代


Plan.product_type = _compat_product_type
Plan.capacity = _compat_capacity
Plan.price_cents = _compat_price_cents
Plan.skill_level = _compat_skill_level
Plan.occupation_name = _compat_occupation_name
Plan.occupation_code = _compat_occupation_code
Plan.exam_type = _compat_exam_type
Plan.application_type = _compat_application_type
Plan.renshe_config = _compat_renshe_config
Plan.renshe_config_id = _compat_renshe_config_id
Plan.project_name = _compat_project_name
