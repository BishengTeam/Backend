"""H3C 批次多产品改造：价格从批次级移到产品级。

Revision ID: hbp001
Revises: cp021_competition_quiz_library
Create Date: 2026-11-13
"""
from alembic import op
import sqlalchemy as sa

revision = "hbp001"
down_revision = "cp021_competition_quiz_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. cert_product 加三档价列（nullable，H3C 产品必填由应用层校验）
    op.add_column("cert_product",
        sa.Column("coupon_price_cents", sa.Integer(), nullable=True))
    op.add_column("cert_product",
        sa.Column("student_price_cents", sa.Integer(), nullable=True))
    op.add_column("cert_product",
        sa.Column("full_price_cents", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_cert_product_prices_nonneg", "cert_product",
        "coupon_price_cents IS NULL OR coupon_price_cents >= 0")
    op.create_check_constraint(
        "ck_cert_product_student_price_nonneg", "cert_product",
        "student_price_cents IS NULL OR student_price_cents >= 0")
    op.create_check_constraint(
        "ck_cert_product_full_price_nonneg", "cert_product",
        "full_price_cents IS NULL OR full_price_cents >= 0")

    # 2. 新表 h3c_batch_product（纯关联，无价格）
    op.create_table(
        "h3c_batch_product",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("batch_id", sa.Integer(),
                  sa.ForeignKey("h3c_exam_batch.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Integer(),
                  sa.ForeignKey("cert_product.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "product_id", name="uq_h3c_batch_product"),
    )

    # 3. plan.product_type 变 nullable（H3C 多产品批次无 product_type）
    op.alter_column("plan", "product_type", nullable=True)

    # 唯一索引改为部分索引（product_type IS NOT NULL 时生效）
    op.drop_index("uq_plan_product_name", table_name="plan")
    op.create_index(
        "uq_plan_product_name", "plan",
        ["product_type", "name"], unique=True,
        postgresql_where=sa.text("product_type IS NOT NULL"))
    # H3C 多产品批次（product_type IS NULL）只要求 name 唯一
    op.create_index(
        "uq_plan_name_multi", "plan", ["name"], unique=True,
        postgresql_where=sa.text("product_type IS NULL"))

    # 4. h3c_registration 加 batch_product_id
    op.add_column("h3c_registration",
        sa.Column("batch_product_id", sa.Integer(),
                  sa.ForeignKey("h3c_batch_product.id", ondelete="RESTRICT"), nullable=True))

    # 唯一约束改为：同批次同产品同身份证唯一
    op.drop_index(
        "uq_h3c_registration_active_batch_idcard",
        table_name="h3c_registration")
    op.create_index(
        "uq_h3c_reg_active_product_idcard",
        "h3c_registration",
        ["batch_product_id", "candidate_idcard"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('pending_payment','pending_review',"
            "'rejected_awaiting_resubmission',"
            "'pending_refund_confirmation','refund_processing',"
            "'approved','final_approved')"))

    # 5. 存量数据迁移：从 h3c_exam_batch 的 plan → cert_product 反填价格
    #    （存量批次的产品价格从批次表复制到产品表）
    op.execute("""
        UPDATE cert_product SET
            coupon_price_cents = sub.coupon_price_cents,
            student_price_cents = sub.student_price_cents,
            full_price_cents = sub.full_price_cents
        FROM (
            SELECT DISTINCT ON (cp.id)
                cp.id,
                heb.coupon_price_cents,
                heb.student_price_cents,
                heb.full_price_cents
            FROM cert_product cp
            JOIN plan p ON p.product_type = cp.code
            JOIN h3c_exam_batch heb ON heb.plan_id = p.id
            WHERE cp.type = 'h3c'
            ORDER BY cp.id, heb.id DESC
        ) sub
        WHERE cert_product.id = sub.id
          AND cert_product.coupon_price_cents IS NULL
    """)

    # 6. 存量 registration 关联到 h3c_batch_product
    #    （通过 batch_id + plan 的 product_type 推导 product_id）
    op.execute("""
        INSERT INTO h3c_batch_product (batch_id, product_id, created_at, updated_at)
        SELECT DISTINCT heb.id, cp.id, now(), now()
        FROM h3c_exam_batch heb
        JOIN plan p ON p.id = heb.plan_id
        JOIN cert_product cp ON cp.code = p.product_type
        WHERE cp.type = 'h3c'
        ON CONFLICT DO NOTHING
    """)

    op.execute("""
        UPDATE h3c_registration reg
        SET batch_product_id = hbp.id
        FROM h3c_batch_product hbp
        WHERE reg.batch_id = hbp.batch_id
          AND reg.batch_product_id IS NULL
    """)


def downgrade() -> None:
    # 恢复唯一索引
    op.drop_index("uq_h3c_reg_active_product_idcard", table_name="h3c_registration")
    op.create_index(
        "uq_h3c_registration_active_batch_idcard",
        "h3c_registration",
        ["batch_id", "candidate_idcard"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('pending_payment','pending_review',"
            "'rejected_awaiting_resubmission',"
            "'pending_refund_confirmation','refund_processing',"
            "'approved','final_approved')"))

    op.drop_column("h3c_registration", "batch_product_id")

    # 恢复 plan 唯一索引
    op.drop_index("uq_plan_name_multi", table_name="plan")
    op.drop_index("uq_plan_product_name", table_name="plan")
    op.create_index("uq_plan_product_name", "plan", ["product_type", "name"], unique=True)
    op.alter_column("plan", "product_type", nullable=False)

    op.drop_table("h3c_batch_product")

    op.drop_constraint("ck_cert_product_prices_nonneg", "cert_product")
    op.drop_constraint("ck_cert_product_student_price_nonneg", "cert_product")
    op.drop_constraint("ck_cert_product_full_price_nonneg", "cert_product")
    op.drop_column("cert_product", "coupon_price_cents")
    op.drop_column("cert_product", "student_price_cents")
    op.drop_column("cert_product", "full_price_cents")
