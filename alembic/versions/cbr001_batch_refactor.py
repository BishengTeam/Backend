"""认证批次重构：Plan 从单产品计划变为跨厂商批次。

破坏性迁移（方案 A）：清空所有认证相关历史数据后重建表结构。
不可回滚。

Revision ID: cbr001
Revises: quiz020
Create Date: 2026-11-09
"""
from alembic import op
import sqlalchemy as sa

revision = "cbr001"
down_revision = "quiz020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ================================================================
    # 1. 清空所有引用 plan 的认证相关数据（方案 A：全量丢弃）
    # ================================================================
    op.execute("""
        TRUNCATE TABLE
            h3c_registration,
            nisp_registration,
            h3c_exam_batch,
            nisp_exam_batch,
            renshe_audit_log,
            renshe_application,
            cert_consultation_group,
            certification_journey_content
        CASCADE
    """)

    # 清空认证类订单（保留课程/积分商城等非认证订单）
    op.execute("""DELETE FROM "order" WHERE order_kind = 'certification'""")

    # 清空 H3C/NISP 衍生表（审核、材料、退款、导出、修正、版本）
    # 这些表通过 FK RESTRICT 引用 registration，TRUNCATE CASCADE 已覆盖；
    # 此处显式清理无 FK 关联的残留。
    op.execute("""TRUNCATE TABLE h3c_material, h3c_material_upload, h3c_review,
        h3c_refund_request, h3c_registration_version, h3c_correction_request,
        h3c_export_job, h3c_export_item, h3c_final_review CASCADE""")
    op.execute("""TRUNCATE TABLE nisp_material_file, nisp_review,
        nisp_refund_request, nisp_registration_version, nisp_correction_request,
        nisp_export_job, nisp_export_item, nisp_final_review CASCADE""")

    # ================================================================
    # 2. 重建 plan 表
    # ================================================================
    op.execute("""DROP TABLE IF EXISTS plan CASCADE""")

    op.create_table(
        "plan",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("apply_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("apply_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exam_date", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("sort_order >= 0", name="ck_plan_sort_order_nonnegative"),
    )
    op.create_index("uq_plan_name", "plan", ["name"], unique=True)
    op.create_index("ix_plan_status", "plan", ["status"])

    # ================================================================
    # 3. 新表 plan_product
    # ================================================================
    op.create_table(
        "plan_product",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("plan_id", sa.BigInteger(), sa.ForeignKey("plan.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("cert_product.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("renshe_config_id", sa.BigInteger(), sa.ForeignKey("renshe_project_config.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("renshe_config", sa.JSON(), nullable=True),
        sa.Column("occupation_name", sa.String(64), nullable=True),
        sa.Column("occupation_code", sa.String(160), nullable=True),
        sa.Column("skill_level", sa.String(32), nullable=True),
        sa.Column("exam_type", sa.String(16), nullable=True),
        sa.Column("application_type", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "product_id", name="uq_plan_product"),
        sa.CheckConstraint("capacity >= 0", name="ck_plan_product_capacity"),
    )
    op.create_index("ix_plan_product_product", "plan_product", ["product_id"])
    op.create_index("ix_plan_product_plan", "plan_product", ["plan_id"])

    # ================================================================
    # 4. 新表 plan_time_segment
    # ================================================================
    op.create_table(
        "plan_time_segment",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("plan_id", sa.BigInteger(), sa.ForeignKey("plan.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.String(32), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "label", name="uq_plan_segment_label"),
        sa.CheckConstraint("end_time > start_time", name="ck_segment_time"),
        sa.CheckConstraint("sort_order >= 0", name="ck_segment_sort_nonnegative"),
    )
    op.create_index("ix_plan_segment_plan", "plan_time_segment", ["plan_id", "sort_order"])

    # ================================================================
    # 5. H3C 厂商表：plan_id → plan_product_id
    # ================================================================
    op.execute("""ALTER TABLE h3c_exam_batch DROP CONSTRAINT IF EXISTS uq_h3c_batch_plan""")
    op.execute("""ALTER TABLE h3c_exam_batch DROP COLUMN IF EXISTS plan_id""")
    op.add_column("h3c_exam_batch",
        sa.Column("plan_product_id", sa.Integer(),
                  sa.ForeignKey("plan_product.id", ondelete="RESTRICT"), nullable=False))
    op.create_unique_constraint("uq_h3c_batch_plan_product", "h3c_exam_batch", ["plan_product_id"])

    op.execute("""ALTER TABLE h3c_registration DROP COLUMN IF EXISTS plan_id""")
    op.add_column("h3c_registration",
        sa.Column("plan_product_id", sa.Integer(),
                  sa.ForeignKey("plan_product.id", ondelete="RESTRICT"), nullable=False))

    # ================================================================
    # 6. NISP 厂商表：plan_id → plan_product_id
    # ================================================================
    op.execute("""ALTER TABLE nisp_exam_batch DROP CONSTRAINT IF EXISTS uq_nisp_batch_plan""")
    op.execute("""ALTER TABLE nisp_exam_batch DROP COLUMN IF EXISTS plan_id""")
    op.add_column("nisp_exam_batch",
        sa.Column("plan_product_id", sa.Integer(),
                  sa.ForeignKey("plan_product.id", ondelete="RESTRICT"), nullable=False))
    op.create_unique_constraint("uq_nisp_batch_plan_product", "nisp_exam_batch", ["plan_product_id"])

    op.execute("""ALTER TABLE nisp_registration DROP COLUMN IF EXISTS plan_id""")
    op.add_column("nisp_registration",
        sa.Column("plan_product_id", sa.Integer(),
                  sa.ForeignKey("plan_product.id", ondelete="RESTRICT"), nullable=False))

    # ================================================================
    # 7. 咨询群：plan_id → plan_product_id
    # ================================================================
    op.execute("""ALTER TABLE cert_consultation_group DROP CONSTRAINT IF EXISTS uq_cert_consultation_group_plan""")
    op.execute("""ALTER TABLE cert_consultation_group DROP COLUMN IF EXISTS plan_id""")
    op.add_column("cert_consultation_group",
        sa.Column("plan_product_id", sa.Integer(),
                  sa.ForeignKey("plan_product.id", ondelete="CASCADE"), nullable=False))
    op.create_unique_constraint("uq_cert_consultation_group_plan_product",
                                "cert_consultation_group", ["plan_product_id"])

    # ================================================================
    # 8. Order 唯一索引：加 product_type 维度
    # ================================================================
    op.execute("""DROP INDEX IF EXISTS uq_order_active_user_plan""")
    op.execute("""
        CREATE UNIQUE INDEX uq_order_active_user_plan_product
        ON "order" (user_id, plan_id, product_type)
        WHERE plan_id IS NOT NULL AND status IN ('pending', 'paid', 'completed')
    """)


def downgrade() -> None:
    """破坏性迁移，不可回滚。"""
    raise RuntimeError(
        "cbr001 is a destructive migration; downgrade is not supported. "
        "Restore from a pre-migration database backup instead."
    )
