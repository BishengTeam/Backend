"""Points-based virtual entitlement products and redemption orders.

Revision ID: r60001
Revises: r50001
Create Date: 2026-10-12
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "r60001"
down_revision = "r50001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "points_virtual_product",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("product_type", sa.String(length=24), nullable=False),
        sa.Column("content_key", sa.String(length=80), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=True),
        sa.Column("library_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("cover_url", sa.String(length=512), nullable=True),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column("points_price", sa.Integer(), nullable=False),
        sa.Column("stock_limit", sa.Integer(), nullable=True),
        sa.Column("total_redeemed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("entitlement_days", sa.Integer(), nullable=True),
        sa.Column("available_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("content_snapshot", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_key", name="uq_points_virtual_product_content_key"),
        sa.ForeignKeyConstraint(["course_id"], ["course.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["library_id"], ["quiz_library.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("product_type IN ('course', 'quiz_library')", name="ck_points_virtual_product_type"),
        sa.CheckConstraint("status IN ('draft', 'published', 'offline')", name="ck_points_virtual_product_status"),
        sa.CheckConstraint("points_price > 0", name="ck_points_virtual_price_positive"),
        sa.CheckConstraint("total_redeemed >= 0", name="ck_points_virtual_redeemed_nonnegative"),
        sa.CheckConstraint("stock_limit IS NULL OR stock_limit >= 0", name="ck_points_virtual_stock_nonnegative"),
        sa.CheckConstraint("stock_limit IS NULL OR total_redeemed <= stock_limit", name="ck_points_virtual_stock_not_oversold"),
        sa.CheckConstraint("entitlement_days IS NULL OR entitlement_days > 0", name="ck_points_virtual_entitlement_days"),
        sa.CheckConstraint("version >= 1", name="ck_points_virtual_version_positive"),
        sa.CheckConstraint(
            "((product_type = 'course' AND course_id IS NOT NULL AND library_id IS NULL) OR "
            "(product_type = 'quiz_library' AND library_id IS NOT NULL AND course_id IS NULL))",
            name="ck_points_virtual_product_content",
        ),
    )
    op.create_index(
        "ix_points_virtual_product_status",
        "points_virtual_product",
        ["status", "id"],
    )

    op.create_table(
        "points_virtual_order",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("order_no", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("product_type", sa.String(length=24), nullable=False),
        sa.Column("product_name", sa.String(length=128), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=True),
        sa.Column("library_id", sa.BigInteger(), nullable=True),
        sa.Column("points_cost", sa.Integer(), nullable=False),
        sa.Column("entitlement_days", sa.Integer(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="completed"),
        sa.Column("points_history_id", sa.BigInteger(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_no", name="uq_points_virtual_order_no"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_points_virtual_user_idempotency"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["product_id"], ["points_virtual_product.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("status IN ('completed', 'refunded')", name="ck_points_virtual_order_status"),
        sa.CheckConstraint("points_cost > 0", name="ck_points_virtual_order_cost_positive"),
        sa.CheckConstraint("ends_at IS NULL OR ends_at > starts_at", name="ck_points_virtual_order_period"),
    )
    op.create_index("ix_points_virtual_order_user", "points_virtual_order", ["user_id", "id"])
    op.create_index("ix_points_virtual_order_product", "points_virtual_order", ["product_id", "id"])

    op.create_table(
        "points_virtual_refund",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("points_returned", sa.Integer(), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("reason_detail", sa.String(length=500), nullable=False),
        sa.Column("refunded_by_admin_id", sa.Integer(), nullable=False),
        sa.Column("points_history_id", sa.BigInteger(), nullable=False),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", name="uq_points_virtual_refund_order"),
        sa.ForeignKeyConstraint(["order_id"], ["points_virtual_order.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["refunded_by_admin_id"], ["admin_user.id"]),
        sa.CheckConstraint("points_returned > 0", name="ck_points_virtual_refund_points_positive"),
    )
    op.create_index("ix_points_virtual_refund_user", "points_virtual_refund", ["user_id", "id"])

    op.add_column(
        "course_enrollment",
        sa.Column("points_virtual_order_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "course_enrollment",
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_course_enrollment_points_virtual_order",
        "course_enrollment",
        "points_virtual_order",
        ["points_virtual_order_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "uq_course_enrollment_points_order",
        "course_enrollment",
        ["points_virtual_order_id"],
        unique=True,
        postgresql_where=sa.text("points_virtual_order_id IS NOT NULL"),
    )

    op.add_column(
        "quiz_library_entitlement",
        sa.Column("points_order_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_quiz_entitlement_points_virtual_order",
        "quiz_library_entitlement",
        "points_virtual_order",
        ["points_order_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "uq_quiz_entitlement_points_order_library",
        "quiz_library_entitlement",
        ["points_order_id", "library_id"],
        unique=True,
        postgresql_where=sa.text("points_order_id IS NOT NULL"),
    )
    op.alter_column("quiz_library_entitlement", "course_id", nullable=True)
    op.drop_constraint(
        "ck_quiz_library_entitlement_source_type",
        "quiz_library_entitlement",
        type_="check",
    )
    op.create_check_constraint(
        "ck_quiz_library_entitlement_source_type",
        "quiz_library_entitlement",
        "source_type IN ('course_order', 'course_enrollment', 'quiz_order', 'points_order')",
    )
    op.drop_constraint(
        "ck_quiz_library_entitlement_source_ref",
        "quiz_library_entitlement",
        type_="check",
    )
    op.create_check_constraint(
        "ck_quiz_library_entitlement_source_ref",
        "quiz_library_entitlement",
        "((source_type IN ('course_order', 'quiz_order') AND order_id IS NOT NULL) OR "
        "(source_type = 'course_enrollment' AND enrollment_id IS NOT NULL) OR "
        "(source_type = 'points_order' AND points_order_id IS NOT NULL))",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_quiz_library_entitlement_source_ref",
        "quiz_library_entitlement",
        type_="check",
    )
    op.create_check_constraint(
        "ck_quiz_library_entitlement_source_ref",
        "quiz_library_entitlement",
        "((source_type IN ('course_order', 'quiz_order') AND order_id IS NOT NULL) OR "
        "(source_type = 'course_enrollment' AND enrollment_id IS NOT NULL))",
    )
    op.drop_constraint(
        "ck_quiz_library_entitlement_source_type",
        "quiz_library_entitlement",
        type_="check",
    )
    op.create_check_constraint(
        "ck_quiz_library_entitlement_source_type",
        "quiz_library_entitlement",
        "source_type IN ('course_order', 'course_enrollment', 'quiz_order')",
    )
    op.execute("DELETE FROM quiz_library_entitlement WHERE points_order_id IS NOT NULL")
    op.alter_column("quiz_library_entitlement", "course_id", nullable=False)
    op.drop_index("uq_quiz_entitlement_points_order_library", table_name="quiz_library_entitlement")
    op.drop_constraint(
        "fk_quiz_entitlement_points_virtual_order",
        "quiz_library_entitlement",
        type_="foreignkey",
    )
    op.drop_column("quiz_library_entitlement", "points_order_id")
    op.drop_index("uq_course_enrollment_points_order", table_name="course_enrollment")
    op.execute("DELETE FROM course_enrollment WHERE points_virtual_order_id IS NOT NULL")
    op.drop_constraint(
        "fk_course_enrollment_points_virtual_order",
        "course_enrollment",
        type_="foreignkey",
    )
    op.drop_column("course_enrollment", "points_virtual_order_id")
    op.drop_column("course_enrollment", "access_expires_at")
    op.drop_table("points_virtual_refund")
    op.drop_table("points_virtual_order")
    op.drop_table("points_virtual_product")
