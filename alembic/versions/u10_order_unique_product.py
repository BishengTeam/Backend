"""U10: order 唯一索引加 product_type 列，多产品批次允许同批次报不同科目

Revision ID: u10_order_unique
Revises: fda5ede (当前 head 由 alembic 动态获取，手动填)
"""
from alembic import op

revision = "u10_order_unique"
down_revision = "hbp001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 先删两列索引
    op.execute("DROP INDEX IF EXISTS uq_order_active_user_plan")
    # 重建三列索引：同用户同批次同科目唯一，不同科目允许多条
    op.execute("""
        CREATE UNIQUE INDEX uq_order_active_user_plan_product
        ON "order" (user_id, plan_id, product_type)
        WHERE plan_id IS NOT NULL AND status IN ('pending', 'paid', 'completed')
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_order_active_user_plan_product")
    op.execute("""
        CREATE UNIQUE INDEX uq_order_active_user_plan
        ON "order" (user_id, plan_id)
        WHERE plan_id IS NOT NULL AND status IN ('pending', 'paid', 'completed')
    """)
