"""H3C 第一期：分时预约考场（机位管控）— 6 张新表。

Revision ID: exam001
Revises: cbr002
Create Date: 2026-11-12
"""
from alembic import op
import sqlalchemy as sa


revision = "exam001"
down_revision = "cbr002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. holiday — 全局假期
    op.create_table(
        "holiday",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("holiday_date", sa.Date(), nullable=False),
        sa.Column("is_annual", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("holiday_date", name="uq_holiday_date"),
    )

    # 2. exam_schedule — 考场开放计划
    op.create_table(
        "exam_schedule",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("vendor", sa.String(32), nullable=False, server_default="h3c"),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("open_date_start", sa.Date(), nullable=False),
        sa.Column("open_date_end", sa.Date(), nullable=False),
        sa.Column("allowed_weekdays", sa.JSON(), nullable=False, server_default="[4]"),
        sa.Column("pre_exam_notice", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("published_at", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("status IN ('draft','published','closed')", name="ck_exam_schedule_status"),
        sa.CheckConstraint("open_date_start <= open_date_end", name="ck_exam_schedule_dates"),
        sa.CheckConstraint("json_array_length(allowed_weekdays) > 0", name="ck_exam_schedule_weekdays_not_empty"),
    )
    op.create_index("ix_exam_schedule_status", "exam_schedule", ["status"])
    op.create_index("ix_exam_schedule_vendor", "exam_schedule", ["vendor"])

    # 3. exam_schedule_subject — 计划包含的科目
    op.create_table(
        "exam_schedule_subject",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("schedule_id", sa.Integer(), sa.ForeignKey("exam_schedule.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("cert_product.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("schedule_id", "product_id", name="uq_exam_schedule_subject"),
    )

    # 4. exam_time_slot — 可预约时段
    op.create_table(
        "exam_time_slot",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("schedule_id", sa.Integer(), sa.ForeignKey("exam_schedule.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.String(32), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("schedule_id", "start_time", name="uq_exam_time_slot"),
        sa.CheckConstraint("sort_order >= 0", name="ck_exam_time_slot_sort"),
    )

    # 5. exam_seat_quota — 机位配额（科目 × 时段）
    op.create_table(
        "exam_seat_quota",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("schedule_id", sa.Integer(), sa.ForeignKey("exam_schedule.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject_id", sa.Integer(), sa.ForeignKey("exam_schedule_subject.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slot_id", sa.Integer(), sa.ForeignKey("exam_time_slot.id", ondelete="CASCADE"), nullable=False),
        sa.Column("total_seats", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("schedule_id", "subject_id", "slot_id", name="uq_exam_seat_quota"),
        sa.CheckConstraint("total_seats >= 0", name="ck_exam_seat_quota_nonneg"),
    )

    # 6. exam_booking — 考生预约记录
    op.create_table(
        "exam_booking",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("registration_id", sa.Integer(), sa.ForeignKey("h3c_registration.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("schedule_id", sa.Integer(), sa.ForeignKey("exam_schedule.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("subject_id", sa.Integer(), sa.ForeignKey("exam_schedule_subject.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("slot_id", sa.Integer(), sa.ForeignKey("exam_time_slot.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("exam_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="booked"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("status IN ('booked','cancelled','completed','no_show')", name="ck_exam_booking_status"),
        sa.UniqueConstraint("registration_id", "schedule_id", name="uq_exam_booking_once"),
    )
    op.create_index(
        "ix_exam_booking_lookup",
        "exam_booking",
        ["schedule_id", "exam_date", "slot_id", "subject_id", "status"],
    )


def downgrade() -> None:
    op.drop_table("exam_booking")
    op.drop_table("exam_seat_quota")
    op.drop_table("exam_time_slot")
    op.drop_table("exam_schedule_subject")
    op.drop_table("exam_schedule")
    op.drop_table("holiday")