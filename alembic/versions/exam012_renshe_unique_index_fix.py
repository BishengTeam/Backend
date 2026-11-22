"""Fix NULL semantics for renshe active application uniqueness.

Revision ID: exam012
Revises: exam011
"""
from alembic import op
import sqlalchemy as sa


revision = "exam012"
down_revision = "exam011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # exam011 之后新建的报名可能还没写 batch_project_id；
    # 对“每批次恰好一个项目”的旧结构再做一次安全回填。
    op.execute(
        """
        INSERT INTO renshe_batch_project
            (plan_id, product_id, config_id, price_cents,
             occupation_name, occupation_code, skill_level)
        SELECT p.id, cp.id, p.renshe_config_id, p.price_cents,
               p.occupation_name, p.occupation_code, p.skill_level
        FROM plan p
        JOIN cert_product cp ON cp.code = p.product_type
        WHERE cp.type = 'renshe'
          AND p.product_type IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM renshe_batch_project bp WHERE bp.plan_id = p.id
          )
        """
    )
    op.execute(
        """
        UPDATE renshe_application a
        SET batch_project_id = pick.project_id
        FROM (
            SELECT plan_id, min(id) AS project_id
            FROM renshe_batch_project
            GROUP BY plan_id
            HAVING count(*) = 1
        ) pick
        WHERE a.plan_id = pick.plan_id AND a.batch_project_id IS NULL
        """
    )
    # 无法从旧 Plan 唯一映射的报名保留为 legacy NULL；服务层会在可判定
    # 单一项目时补写，并由下方的 legacy/project 两个唯一索引共同约束。
    op.drop_index(
        "uq_renshe_application_active_user_plan",
        table_name="renshe_application",
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_renshe_application_active_legacy
        ON renshe_application (user_id, plan_id)
        WHERE status <> 'closed' AND batch_project_id IS NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_renshe_application_active_project
        ON renshe_application (user_id, plan_id, batch_project_id)
        WHERE status <> 'closed' AND batch_project_id IS NOT NULL
        """
    )
    op.create_check_constraint(
        "ck_renshe_batch_project_price",
        "renshe_batch_project",
        "price_cents >= 0",
    )
    op.create_check_constraint(
        "ck_renshe_batch_project_sort",
        "renshe_batch_project",
        "sort_order >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_renshe_batch_project_sort",
        "renshe_batch_project",
        type_="check",
    )
    op.drop_constraint(
        "ck_renshe_batch_project_price",
        "renshe_batch_project",
        type_="check",
    )
    op.drop_index(
        "uq_renshe_application_active_project",
        table_name="renshe_application",
    )
    op.drop_index(
        "uq_renshe_application_active_legacy",
        table_name="renshe_application",
    )
    op.create_index(
        "uq_renshe_application_active_user_plan",
        "renshe_application",
        ["user_id", "plan_id", "batch_project_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'closed'"),
    )
