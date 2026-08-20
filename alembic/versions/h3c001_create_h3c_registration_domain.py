"""Create the H3C certification registration domain.

The generic ``plan`` row remains the single source of application/exam windows,
capacity, and public batch lifecycle. ``h3c_exam_batch`` stores only fields
specific to the official H3C workbook and H3C payment policy.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "h3c001"
down_revision: str | Sequence[str] | None = "crs001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_admin_user_role", "admin_user", type_="check")
    op.create_check_constraint(
        "ck_admin_user_role",
        "admin_user",
        "role IN ('super_admin', 'quiz_admin', 'h3c_admin')",
    )

    op.create_table(
        "h3c_exam_batch",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("exam_code", sa.String(length=64), nullable=False),
        sa.Column("country", sa.String(length=8), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("identity_tag", sa.String(length=64), nullable=False),
        sa.Column("training_org", sa.String(length=128), nullable=True),
        sa.Column("training_teacher", sa.String(length=64), nullable=True),
        sa.Column("training_address", sa.String(length=256), nullable=True),
        sa.Column("training_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("training_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("coupon_price_cents", sa.Integer(), nullable=False),
        sa.Column("student_price_cents", sa.Integer(), nullable=False),
        sa.Column("full_price_cents", sa.Integer(), nullable=False),
        sa.Column("payment_timeout_minutes", sa.Integer(), nullable=False),
        sa.Column("resubmission_window_hours", sa.Integer(), nullable=False),
        sa.Column("max_resubmissions", sa.Integer(), nullable=False),
        sa.Column("max_material_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["plan.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "coupon_price_cents >= 0 AND student_price_cents >= 0 "
            "AND full_price_cents >= 0",
            name="ck_h3c_batch_prices_nonnegative",
        ),
        sa.CheckConstraint(
            "payment_timeout_minutes BETWEEN 1 AND 1440",
            name="ck_h3c_payment_timeout",
        ),
        sa.CheckConstraint(
            "resubmission_window_hours BETWEEN 1 AND 720",
            name="ck_h3c_resubmission_window",
        ),
        sa.CheckConstraint(
            "max_resubmissions BETWEEN 0 AND 10",
            name="ck_h3c_max_resubmissions",
        ),
        sa.CheckConstraint(
            "max_material_bytes BETWEEN 1 AND 20971520",
            name="ck_h3c_material_size",
        ),
        sa.UniqueConstraint("plan_id", name="uq_h3c_batch_plan"),
    )

    op.create_table(
        "h3c_registration",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("registration_no", sa.String(length=40), nullable=False),
        sa.Column("registration_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("candidate_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("candidate_idcard", sa.String(length=20), nullable=False),
        sa.Column("resubmission_count", sa.Integer(), nullable=False),
        sa.Column("rejection_count", sa.Integer(), nullable=False),
        sa.Column("resubmission_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_reason", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["h3c_exam_batch.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["plan_id"], ["plan.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["order_id"], ["order.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "registration_type IN ('coupon', 'student', 'full')",
            name="ck_h3c_registration_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending_payment', 'pending_review', "
            "'rejected_awaiting_resubmission', 'pending_refund_confirmation', "
            "'refund_processing', 'approved', 'refunded_closed', 'cancelled')",
            name="ck_h3c_registration_status",
        ),
        sa.CheckConstraint("resubmission_count >= 0", name="ck_h3c_resubmission_count"),
        sa.CheckConstraint("rejection_count >= 0", name="ck_h3c_rejection_count"),
        sa.UniqueConstraint("registration_no", name="uq_h3c_registration_no"),
        sa.UniqueConstraint("order_id", name="uq_h3c_registration_order"),
    )
    op.create_index(
        "ix_h3c_registration_batch_status",
        "h3c_registration",
        ["batch_id", "status"],
    )
    op.create_index(
        "ix_h3c_registration_user",
        "h3c_registration",
        ["user_id"],
    )
    op.create_index(
        "ix_h3c_registration_type",
        "h3c_registration",
        ["registration_type"],
    )
    op.create_index(
        "ix_h3c_registration_status",
        "h3c_registration",
        ["status"],
    )
    op.create_index(
        "ix_h3c_registration_idcard",
        "h3c_registration",
        ["candidate_idcard"],
    )
    op.create_index(
        "uq_h3c_registration_active_batch_idcard",
        "h3c_registration",
        ["batch_id", "candidate_idcard"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('pending_payment', 'pending_review', "
            "'rejected_awaiting_resubmission', 'pending_refund_confirmation', "
            "'refund_processing', 'approved')"
        ),
    )
    op.create_index(
        "ix_h3c_registration_resubmission_due",
        "h3c_registration",
        ["status", "resubmission_due_at"],
    )

    op.create_table(
        "h3c_material",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("registration_id", sa.BigInteger(), nullable=False),
        sa.Column("material_type", sa.String(length=24), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["registration_id"], ["h3c_registration.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "material_type IN ('coupon_proof', 'student_proof')",
            name="ck_h3c_material_type",
        ),
        sa.CheckConstraint("version_no > 0", name="ck_h3c_material_version_positive"),
        sa.CheckConstraint("size_bytes > 0", name="ck_h3c_material_size_positive"),
        sa.UniqueConstraint("registration_id", "material_type", "version_no", name="uq_h3c_material_version"),
    )
    op.create_index("ix_h3c_material_current", "h3c_material", ["registration_id", "is_current"])

    op.create_table(
        "h3c_material_upload",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("material_type", sa.String(length=24), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "material_type IN ('coupon_proof', 'student_proof')",
            name="ck_h3c_material_upload_type",
        ),
        sa.CheckConstraint("size_bytes > 0", name="ck_h3c_material_upload_size"),
        sa.UniqueConstraint("storage_key", name="uq_h3c_material_upload_key"),
    )
    op.create_index("ix_h3c_material_upload_user", "h3c_material_upload", ["user_id", "created_at"])

    op.create_table(
        "h3c_review",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("registration_id", sa.BigInteger(), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("reason_detail", sa.Text(), nullable=True),
        sa.Column("rejected_material_types", postgresql.JSONB(), nullable=True),
        sa.Column("material_version_ids", postgresql.JSONB(), nullable=True),
        sa.Column("reviewer_admin_id", sa.Integer(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["registration_id"], ["h3c_registration.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewer_admin_id"], ["admin_user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("decision IN ('approved', 'rejected')", name="ck_h3c_review_decision"),
        sa.CheckConstraint(
            "decision <> 'rejected' OR (reason_code IS NOT NULL AND length(trim(reason_code)) > 0)",
            name="ck_h3c_review_rejection_reason",
        ),
    )
    op.create_index("ix_h3c_review_registration", "h3c_review", ["registration_id", "id"])

    op.create_table(
        "h3c_refund_request",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("registration_id", sa.BigInteger(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("request_kind", sa.String(length=24), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("reason_detail", sa.Text(), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("requested_by_admin_id", sa.Integer(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_by_admin_id", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("out_refund_no", sa.String(length=64), nullable=True),
        sa.Column("wechat_refund_id", sa.String(length=64), nullable=True),
        sa.Column("processing_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["registration_id"], ["h3c_registration.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["order_id"], ["order.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by_admin_id"], ["admin_user.id"]),
        sa.ForeignKeyConstraint(["approved_by_admin_id"], ["admin_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "request_kind IN ('review_failed', 'batch_cancelled', 'exception_close')",
            name="ck_h3c_refund_kind",
        ),
        sa.CheckConstraint(
            "status IN ('requested', 'approved', 'processing', 'succeeded', 'failed')",
            name="ck_h3c_refund_status",
        ),
        sa.CheckConstraint("amount_cents >= 0", name="ck_h3c_refund_amount_nonnegative"),
    )
    op.create_index("ix_h3c_refund_status", "h3c_refund_request", ["status", "id"])
    op.create_index(
        "uq_h3c_refund_active_order",
        "h3c_refund_request",
        ["order_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('requested', 'approved', 'processing', 'failed')"),
    )

    op.create_table(
        "h3c_export_job",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("registration_type", sa.String(length=16), nullable=False),
        sa.Column("artifact_type", sa.String(length=16), nullable=False),
        sa.Column("requested_by_admin_id", sa.Integer(), nullable=False),
        sa.Column("include_statuses", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("registration_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("storage_key", sa.String(length=512), nullable=True),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=True),
        sa.Column("artifact_bytes", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["h3c_exam_batch.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by_admin_id"], ["admin_user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_h3c_export_job_status",
        ),
        sa.CheckConstraint(
            "artifact_type IN ('embedded_xlsx', 'images_zip')",
            name="ck_h3c_export_artifact_type",
        ),
        sa.CheckConstraint("registration_count >= 0", name="ck_h3c_export_count_nonnegative"),
    )
    op.create_index("ix_h3c_export_status", "h3c_export_job", ["status", "id"])
    op.create_index("ix_h3c_export_expires", "h3c_export_job", ["status", "expires_at"])

    op.create_table(
        "h3c_export_item",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("registration_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["h3c_export_job.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["registration_id"], ["h3c_registration.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "registration_id", name="uq_h3c_export_item"),
    )
    op.create_index("ix_h3c_export_item_registration", "h3c_export_item", ["registration_id", "id"])


def downgrade() -> None:
    op.drop_table("h3c_export_item")
    op.drop_table("h3c_export_job")
    op.drop_index("uq_h3c_refund_active_order", table_name="h3c_refund_request")
    op.drop_index("ix_h3c_refund_status", table_name="h3c_refund_request")
    op.drop_table("h3c_refund_request")
    op.drop_index("ix_h3c_review_registration", table_name="h3c_review")
    op.drop_table("h3c_review")
    op.drop_index("ix_h3c_material_upload_user", table_name="h3c_material_upload")
    op.drop_table("h3c_material_upload")
    op.drop_index("ix_h3c_material_current", table_name="h3c_material")
    op.drop_table("h3c_material")
    op.drop_index("ix_h3c_registration_resubmission_due", table_name="h3c_registration")
    op.drop_index("uq_h3c_registration_active_batch_idcard", table_name="h3c_registration")
    op.drop_index("ix_h3c_registration_idcard", table_name="h3c_registration")
    op.drop_index("ix_h3c_registration_status", table_name="h3c_registration")
    op.drop_index("ix_h3c_registration_type", table_name="h3c_registration")
    op.drop_index("ix_h3c_registration_user", table_name="h3c_registration")
    op.drop_index("ix_h3c_registration_batch_status", table_name="h3c_registration")
    op.drop_table("h3c_registration")
    op.drop_table("h3c_exam_batch")
    op.drop_constraint("ck_admin_user_role", "admin_user", type_="check")
    op.create_check_constraint(
        "ck_admin_user_role",
        "admin_user",
        "role IN ('super_admin', 'quiz_admin')",
    )
