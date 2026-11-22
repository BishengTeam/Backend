"""Add multi-project renshe batches.

Revision ID: exam011
Revises: exam010
"""
import os

from alembic import op
import sqlalchemy as sa


revision = "exam011"
down_revision = "exam010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "renshe_batch_project",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "plan_id",
            sa.Integer(),
            sa.ForeignKey("plan.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("cert_product.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "config_id",
            sa.Integer(),
            sa.ForeignKey("renshe_project_config.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("price_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("occupation_name", sa.String(length=64), nullable=True),
        sa.Column("occupation_code", sa.String(length=160), nullable=True),
        sa.Column("skill_level", sa.String(length=32), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "product_id", name="uq_renshe_batch_project"),
    )
    op.create_index(
        "ix_renshe_batch_project_plan",
        "renshe_batch_project",
        ["plan_id", "is_active"],
    )

    op.add_column(
        "renshe_application",
        sa.Column(
            "batch_project_id",
            sa.Integer(),
            sa.ForeignKey("renshe_batch_project.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_renshe_application_batch_project",
        "renshe_application",
        ["batch_project_id"],
    )

    # 旧人社批次回填一条项目行，保证历史报名有稳定归属。
    op.execute(
        """
        INSERT INTO renshe_batch_project
            (plan_id, product_id, config_id, price_cents,
             occupation_name, occupation_code, skill_level)
        SELECT p.id, cp.id, p.renshe_config_id, p.price_cents,
               p.occupation_name, p.occupation_code, p.skill_level
        FROM plan p
        JOIN cert_product cp ON cp.code = p.product_type
        WHERE cp.type = 'renshe' AND p.product_type IS NOT NULL
        ON CONFLICT (plan_id, product_id) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE renshe_application a
        SET batch_project_id = bp.id
        FROM renshe_batch_project bp
        WHERE bp.plan_id = a.plan_id AND a.batch_project_id IS NULL
        """
    )

    op.drop_index(
        "uq_renshe_application_active_user_plan",
        table_name="renshe_application",
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_renshe_application_active_user_plan
        ON renshe_application (user_id, plan_id, batch_project_id)
        WHERE status <> 'closed'
        """
    )

    op.drop_index("uq_plan_product_name", table_name="plan")
    op.execute(
        "CREATE UNIQUE INDEX uq_plan_product_name ON plan (product_type, name) "
        "WHERE product_type IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_plan_name_without_product ON plan (name) "
        "WHERE product_type IS NULL"
    )


def downgrade() -> None:
    if not op.get_context().as_sql:
        duplicates = op.get_bind().execute(
            sa.text(
                """
                SELECT count(*) FROM (
                    SELECT user_id, plan_id
                    FROM renshe_application
                    WHERE status <> 'closed'
                    GROUP BY user_id, plan_id
                    HAVING count(*) > 1
                ) duplicate_groups
                """
            )
        ).scalar_one()
        if duplicates and os.getenv("ALLOW_DESTRUCTIVE_EXAM_DOWNGRADE") != "1":
            raise RuntimeError(
                f"exam011 downgrade would violate the legacy "
                f"(user_id, plan_id) uniqueness for {duplicates} groups; "
                "set ALLOW_DESTRUCTIVE_EXAM_DOWNGRADE=1 after resolving them"
            )

    op.execute("DROP INDEX IF EXISTS uq_plan_name_without_product")
    op.drop_index("uq_plan_product_name", table_name="plan")
    op.create_index(
        "uq_plan_product_name", "plan", ["product_type", "name"], unique=True
    )

    op.drop_index(
        "uq_renshe_application_active_user_plan",
        table_name="renshe_application",
    )
    op.create_index(
        "uq_renshe_application_active_user_plan",
        "renshe_application",
        ["user_id", "plan_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'closed'"),
    )
    op.drop_index(
        "ix_renshe_application_batch_project",
        table_name="renshe_application",
    )
    op.drop_column("renshe_application", "batch_project_id")
    op.drop_index(
        "ix_renshe_batch_project_plan", table_name="renshe_batch_project"
    )
    op.drop_table("renshe_batch_project")
