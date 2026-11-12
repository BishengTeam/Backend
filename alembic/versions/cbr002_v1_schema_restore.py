"""认证模块回退：v2 总批次 schema → v1 各认证独立批次 schema。

前向恢复迁移（不 downgrade cbr001），将 plan 表恢复为 v1 单产品结构。
v2 期间创建的认证数据将被清空。

Revision ID: cbr002
Revises: quiz021
Create Date: 2026-11-11
"""
from alembic import op
import sqlalchemy as sa

revision = "cbr002"
down_revision = "quiz021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ================================================================
    # 1. 清空 v2 认证数据（保留非认证数据）
    # ================================================================
    op.execute("""
        TRUNCATE TABLE
            h3c_registration, nisp_registration,
            h3c_exam_batch, nisp_exam_batch,
            cert_consultation_group,
            certification_journey_content
        CASCADE
    """)
    # 先删除引用 order 的全部子表行，避免 FK 约束阻塞
    for child_table in (
        "inventory_record",
        "course_enrollment",
        "h3c_refund_request",
        "nisp_refund_request",
        "renshe_refund_request",
        "offline_bank_entry",
        "offline_payment_audit",
        "offline_payment_material",
        "offline_payment_submission",
        "offline_payment",
        "payment_refund_task",
        "points_mall_redemption",
        "quiz_library_entitlement",
    ):
        op.execute(f"""
            DELETE FROM {child_table}
            WHERE order_id IN (SELECT id FROM "order" WHERE order_kind = 'certification')
        """)
    op.execute("""DELETE FROM "order" WHERE order_kind = 'certification'""")

    # ================================================================
    # 2. 删除 v2 新表
    # ================================================================
    op.execute("DROP TABLE IF EXISTS plan_time_segment CASCADE")
    op.execute("DROP TABLE IF EXISTS plan_product CASCADE")

    # ================================================================
    # 3. 重建 v1 plan 表
    # ================================================================
    op.execute("DROP TABLE IF EXISTS plan CASCADE")
    op.create_table(
        "plan",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_type", sa.String(64), nullable=False),
        sa.Column("renshe_config_id", sa.Integer(), sa.ForeignKey("renshe_project_config.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("renshe_config", sa.JSON(), nullable=True),
        sa.Column("project_name", sa.String(128), nullable=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("apply_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("apply_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exam_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("capacity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("occupation_name", sa.String(64), nullable=False, server_default="信息安全管理员"),
        sa.Column("occupation_code", sa.String(160), nullable=False, server_default="4-04-04-02-网络与信息安全管理员-信息安全管理员-中级工"),
        sa.Column("skill_level", sa.String(32), nullable=False, server_default="中级工"),
        sa.Column("exam_type", sa.String(16), nullable=False, server_default="新考"),
        sa.Column("application_type", sa.String(16), nullable=False, server_default="学历型"),
        sa.Column("price_cents", sa.Integer(), nullable=False, server_default="50000"),
        sa.Column("exam_location", sa.String(256), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("contact_name", sa.String(64), nullable=True),
        sa.Column("contact_phone", sa.String(20), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("registration_closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleanup_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('draft','published','registration_closed','finalized','archived','cancelled')",
            name="ck_plan_status",
        ),
        sa.CheckConstraint("price_cents >= 0", name="ck_plan_price_nonnegative"),
        sa.CheckConstraint("capacity >= 0", name="ck_plan_capacity_nonnegative"),
        sa.CheckConstraint("sort_order >= 0", name="ck_plan_sort_order_nonnegative"),
    )
    op.create_index("uq_plan_product_name", "plan", ["product_type", "name"], unique=True)
    op.create_index("ix_plan_status", "plan", ["status"])
    op.create_index("ix_plan_product_type", "plan", ["product_type"])

    # ================================================================
    # 4. H3C/NISP 批次表恢复 plan_id
    # ================================================================
    # H3C
    op.execute("ALTER TABLE h3c_exam_batch DROP COLUMN IF EXISTS plan_product_id")
    op.add_column("h3c_exam_batch",
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("plan.id", ondelete="RESTRICT"), nullable=False))
    op.create_unique_constraint("uq_h3c_batch_plan", "h3c_exam_batch", ["plan_id"])

    # H3C Registration 恢复 plan_id
    op.execute("ALTER TABLE h3c_registration DROP COLUMN IF EXISTS plan_product_id")
    op.add_column("h3c_registration",
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("plan.id", ondelete="RESTRICT"), nullable=False))

    # NISP
    op.execute("ALTER TABLE nisp_exam_batch DROP COLUMN IF EXISTS plan_product_id")
    op.add_column("nisp_exam_batch",
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("plan.id", ondelete="RESTRICT"), nullable=False))
    op.create_unique_constraint("uq_nisp_batch_plan", "nisp_exam_batch", ["plan_id"])

    # NISP Registration 恢复 plan_id
    op.execute("ALTER TABLE nisp_registration DROP COLUMN IF EXISTS plan_product_id")
    op.add_column("nisp_registration",
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("plan.id", ondelete="RESTRICT"), nullable=False))

    # ================================================================
    # 5. 咨询群恢复 plan_id
    # ================================================================
    op.execute("ALTER TABLE cert_consultation_group DROP COLUMN IF EXISTS plan_product_id")
    op.add_column("cert_consultation_group",
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("plan.id", ondelete="CASCADE"), nullable=False))
    op.create_unique_constraint("uq_cert_consultation_group_plan", "cert_consultation_group", ["plan_id"])

    # ================================================================
    # 6. Order 恢复旧唯一索引（两列）
    # ================================================================
    op.execute("DROP INDEX IF EXISTS uq_order_active_user_plan_product")
    op.execute("""
        CREATE UNIQUE INDEX uq_order_active_user_plan
        ON "order" (user_id, plan_id)
        WHERE plan_id IS NOT NULL AND status IN ('pending', 'paid', 'completed')
    """)


def downgrade() -> None:
    """前向恢复迁移，不可逆。"""
    raise RuntimeError(
        "cbr002 is a destructive rollback migration; downgrade is not supported. "
        "This migration restores v1 schema by clearing v2 certification data."
    )
