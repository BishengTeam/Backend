"""order_refactor

Revision ID: o001_order_refactor
Revises: t001_create_training
Create Date: 2026-06-19
"""
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = 'o001_order_refactor'
down_revision: Union[str, Sequence[str], None] = 't001_create_training'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if context.is_offline_mode():
        columns = {"candidate_name", "candidate_phone", "cert_type"}
        pc_columns = {"cert_type"}
    else:
        inspector = sa.inspect(op.get_bind())
        columns = {c["name"] for c in inspector.get_columns("order")}
        pc_columns = {c["name"] for c in inspector.get_columns("price_config")}

    # 1. 加 order_kind 列
    if "order_kind" not in columns:
        op.add_column("order",
            sa.Column("order_kind", sa.String(32), nullable=False,
                      server_default="certification"))
        op.create_index("ix_order_order_kind", "order", ["order_kind"])

    # 2. cert_type → product_type
    if "product_type" not in columns:
        # 先加 product_type 列
        op.add_column("order",
            sa.Column("product_type", sa.String(64), nullable=True))
        # 从 cert_type 复制数据
        op.execute("UPDATE \"order\" SET product_type = cert_type")
        # 设为 NOT NULL
        op.alter_column("order", "product_type", nullable=False)
        # 删除旧的 cert_type 列
        if "cert_type" in columns:
            op.drop_column("order", "cert_type")

    # 3. candidate_name 改为 nullable
    op.alter_column("order", "candidate_name", nullable=True)
    op.alter_column("order", "candidate_phone", nullable=True)

    # 4. price_config: cert_type → product_type
    if "product_type" not in pc_columns:
        op.add_column("price_config",
            sa.Column("product_type", sa.String(64), nullable=True))
        op.execute("UPDATE price_config SET product_type = cert_type")
        op.alter_column("price_config", "product_type", nullable=False)
        if "cert_type" in pc_columns:
            # 删除旧唯一索引（如果存在）
            try:
                op.drop_index("uq_price_config_active_cert_user", table_name="price_config")
            except Exception:
                pass
            op.drop_column("price_config", "cert_type")
        op.create_index(
            "uq_price_config_active_product_user",
            "price_config",
            ["product_type", "user_type"],
            unique=True,
            postgresql_where=sa.text("is_active IS TRUE"),
        )


def downgrade() -> None:
    if context.is_offline_mode():
        columns = {"candidate_name", "candidate_phone", "product_type", "order_kind"}
        pc_columns = {"product_type"}
    else:
        inspector = sa.inspect(op.get_bind())
        columns = {c["name"] for c in inspector.get_columns("order")}
        pc_columns = {c["name"] for c in inspector.get_columns("price_config")}

    # price_config: product_type → cert_type
    if "cert_type" not in pc_columns:
        op.add_column("price_config",
            sa.Column("cert_type", sa.String(64), nullable=True))
        op.execute("UPDATE price_config SET cert_type = product_type")
        op.alter_column("price_config", "cert_type", nullable=False)
        try:
            op.drop_index("uq_price_config_active_product_user", table_name="price_config")
        except Exception:
            pass
        op.drop_column("price_config", "product_type")
        op.create_index(
            "uq_price_config_active_cert_user",
            "price_config",
            ["cert_type", "user_type"],
            unique=True,
            postgresql_where=sa.text("is_active IS TRUE"),
        )

    # order: product_type → cert_type
    if "cert_type" not in columns:
        op.add_column("order",
            sa.Column("cert_type", sa.String(64), nullable=True))
        op.execute("UPDATE \"order\" SET cert_type = product_type")
        op.alter_column("order", "cert_type", nullable=False)
        op.drop_column("order", "product_type")

    # candidate_* → NOT NULL
    op.alter_column("order", "candidate_name", nullable=False)
    op.alter_column("order", "candidate_phone", nullable=False)

    # 删 order_kind
    if "order_kind" in columns:
        op.drop_index("ix_order_order_kind", table_name="order")
        op.drop_column("order", "order_kind")
