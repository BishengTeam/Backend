"""add dedicated human-resources registration domain

Revision ID: rsh001
Revises: c6d7e8f9a0b2
Create Date: 2026-08-05 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "rsh001"
down_revision: Union[str, Sequence[str], None] = "c6d7e8f9a0b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamp_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.drop_constraint("ck_admin_user_role", "admin_user", type_="check")
    op.execute(
        "UPDATE admin_user SET role = 'admin' "
        "WHERE role NOT IN ('super_admin', 'admin')"
    )
    op.alter_column(
        "admin_user",
        "role",
        existing_type=sa.String(length=32),
        nullable=False,
        server_default="admin",
    )
    op.create_check_constraint(
        "ck_admin_user_role",
        "admin_user",
        "role IN ('super_admin', 'admin')",
    )

    op.drop_constraint("ck_plan_status", "plan", type_="check")
    op.add_column(
        "plan",
        sa.Column(
            "occupation_name",
            sa.String(length=64),
            server_default="信息安全管理员",
            nullable=False,
        ),
    )
    op.add_column(
        "plan",
        sa.Column(
            "occupation_code",
            sa.String(length=160),
            server_default="4-04-04-02-网络与信息安全管理员-信息安全管理员-中级工",
            nullable=False,
        ),
    )
    op.add_column(
        "plan",
        sa.Column("skill_level", sa.String(length=32), server_default="中级工", nullable=False),
    )
    op.add_column(
        "plan",
        sa.Column("exam_type", sa.String(length=16), server_default="新考", nullable=False),
    )
    op.add_column(
        "plan",
        sa.Column(
            "application_type", sa.String(length=16), server_default="学历型", nullable=False
        ),
    )
    op.add_column(
        "plan",
        sa.Column("price_cents", sa.Integer(), server_default="50000", nullable=False),
    )
    op.add_column("plan", sa.Column("exam_location", sa.String(length=256), nullable=True))
    op.add_column("plan", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("plan", sa.Column("contact_name", sa.String(length=64), nullable=True))
    op.add_column("plan", sa.Column("contact_phone", sa.String(length=20), nullable=True))
    op.add_column(
        "plan", sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column("plan", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "plan", sa.Column("registration_closed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("plan", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("plan", sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("plan", sa.Column("cleanup_due_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_plan_status",
        "plan",
        "status IN ('draft', 'published', 'registration_closed', "
        "'finalized', 'archived', 'cancelled')",
    )
    op.create_check_constraint("ck_plan_price_nonnegative", "plan", "price_cents >= 0")
    op.create_check_constraint("ck_plan_capacity_nonnegative", "plan", "capacity >= 0")
    op.create_check_constraint("ck_plan_sort_order_nonnegative", "plan", "sort_order >= 0")

    op.add_column("user_realname", sa.Column("id_card_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "user_realname", sa.Column("active_id_card_hash", sa.String(length=64), nullable=True)
    )
    op.create_index("ix_user_realname_id_card_hash", "user_realname", ["id_card_hash"])
    op.create_index(
        "uq_user_realname_active_id_card_hash",
        "user_realname",
        ["active_id_card_hash"],
        unique=True,
    )
    op.add_column("user_student", sa.Column("enrollment_date", sa.Date(), nullable=True))

    op.create_table(
        "deleted_identity_hash",
        sa.Column("former_user_id", sa.Integer(), nullable=False),
        sa.Column("id_card_hash", sa.String(length=64), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_deleted_identity_hash_former_user_id",
        "deleted_identity_hash",
        ["former_user_id"],
    )
    op.create_index(
        "ix_deleted_identity_hash_id_card_hash",
        "deleted_identity_hash",
        ["id_card_hash"],
    )

    op.create_table(
        "renshe_application",
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("current_version_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="draft", nullable=False),
        sa.Column("draft_data", sa.JSON(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("initial_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("freeze_reason", sa.String(length=64), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_reason", sa.String(length=128), nullable=True),
        sa.Column("sensitive_cleared_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "status IN ('draft', 'pending_payment', 'pending_initial_review', "
            "'initial_rejected', 'pending_external_review', "
            "'external_rejected', 'external_approved', 'closed')",
            name="ck_renshe_application_status",
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["plan.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_renshe_application_plan_id", "renshe_application", ["plan_id"])
    op.create_index("ix_renshe_application_user_id", "renshe_application", ["user_id"])
    op.create_index("ix_renshe_application_status", "renshe_application", ["status"])
    op.create_index(
        "ix_renshe_application_plan_status", "renshe_application", ["plan_id", "status"]
    )
    op.create_index(
        "ix_renshe_application_user_status", "renshe_application", ["user_id", "status"]
    )
    op.create_index(
        "uq_renshe_application_active_user_plan",
        "renshe_application",
        ["user_id", "plan_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'closed'"),
    )

    op.create_table(
        "renshe_application_version",
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("submitted_by_user_id", sa.Integer(), nullable=False),
        sa.Column("realname_snapshot", sa.JSON(), nullable=False),
        sa.Column("student_snapshot", sa.JSON(), nullable=False),
        sa.Column("form_data", sa.JSON(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sensitive_cleared_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamp_columns(),
        sa.ForeignKeyConstraint(
            ["application_id"], ["renshe_application.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["submitted_by_user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "application_id", "version_no", name="uq_renshe_version_application_no"
        ),
    )
    op.create_index(
        "ix_renshe_application_version_application_id",
        "renshe_application_version",
        ["application_id"],
    )
    op.create_index(
        "ix_renshe_version_application",
        "renshe_application_version",
        ["application_id", "version_no"],
    )
    op.create_foreign_key(
        "fk_renshe_application_current_version",
        "renshe_application",
        "renshe_application_version",
        ["current_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "renshe_material",
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("source_storage_key", sa.String(length=512), nullable=True),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("extension", sa.String(length=8), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "kind IN ('id_card_front', 'id_card_back', 'portrait', "
            "'student_card', 'xuexin_registration', 'education_proof')",
            name="ck_renshe_material_kind",
        ),
        sa.CheckConstraint("size_bytes > 0", name="ck_renshe_material_size_positive"),
        sa.ForeignKeyConstraint(
            ["application_id"], ["renshe_application.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["version_id"], ["renshe_application_version.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index("ix_renshe_material_application_id", "renshe_material", ["application_id"])
    op.create_index("ix_renshe_material_version_id", "renshe_material", ["version_id"])
    op.create_index(
        "ix_renshe_material_application",
        "renshe_material",
        ["application_id", "version_id"],
    )
    op.create_index(
        "uq_renshe_material_version_kind",
        "renshe_material",
        ["version_id", "kind"],
        unique=True,
        postgresql_where=sa.text("version_id IS NOT NULL"),
    )

    op.create_table(
        "renshe_review",
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("required_changes", sa.JSON(), nullable=True),
        sa.Column("reviewer_id", sa.Integer(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamp_columns(),
        sa.CheckConstraint("stage IN ('initial', 'external')", name="ck_renshe_review_stage"),
        sa.CheckConstraint(
            "decision IN ('approved', 'rejected')", name="ck_renshe_review_decision"
        ),
        sa.CheckConstraint(
            "decision <> 'rejected' OR (reason IS NOT NULL AND length(trim(reason)) > 0)",
            name="ck_renshe_review_rejection_reason",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"], ["renshe_application.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["version_id"], ["renshe_application_version.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["reviewer_id"], ["admin_user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_renshe_review_application_stage", "renshe_review", ["application_id", "stage"]
    )

    op.create_table(
        "renshe_review_correction",
        sa.Column("review_id", sa.Integer(), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("corrected_by_admin_id", sa.Integer(), nullable=False),
        sa.Column("from_decision", sa.String(length=16), nullable=False),
        sa.Column("to_decision", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("state_before", sa.JSON(), nullable=False),
        sa.Column("state_after", sa.JSON(), nullable=False),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "from_decision IN ('approved', 'rejected')",
            name="ck_renshe_correction_from_decision",
        ),
        sa.CheckConstraint(
            "to_decision IN ('approved', 'rejected')",
            name="ck_renshe_correction_to_decision",
        ),
        sa.CheckConstraint(
            "from_decision <> to_decision", name="ck_renshe_correction_changes_decision"
        ),
        sa.ForeignKeyConstraint(["review_id"], ["renshe_review.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["application_id"], ["renshe_application.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["corrected_by_admin_id"], ["admin_user.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.add_column("order", sa.Column("application_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_order_application_id_renshe_application",
        "order",
        "renshe_application",
        ["application_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_order_application_id", "order", ["application_id"])
    op.create_index(
        "uq_order_active_application",
        "order",
        ["application_id"],
        unique=True,
        postgresql_where=sa.text(
            "application_id IS NOT NULL AND status IN ('pending', 'paid', 'completed')"
        ),
    )

    op.create_table(
        "renshe_refund_request",
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("request_kind", sa.String(length=24), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("reason_detail", sa.Text(), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="requested", nullable=False),
        sa.Column("previous_application_status", sa.String(length=32), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_by_admin_id", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("out_refund_no", sa.String(length=64), nullable=True),
        sa.Column("wechat_refund_id", sa.String(length=64), nullable=True),
        sa.Column("processing_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "request_kind IN ('normal', 'exception', 'batch_cancel', 'batch_finalize')",
            name="ck_renshe_refund_kind",
        ),
        sa.CheckConstraint(
            "status IN ('requested', 'approved', 'processing', 'succeeded', "
            "'rejected', 'failed')",
            name="ck_renshe_refund_status",
        ),
        sa.CheckConstraint("amount_cents > 0", name="ck_renshe_refund_amount_positive"),
        sa.ForeignKeyConstraint(
            ["application_id"], ["renshe_application.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["order_id"], ["order.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["approved_by_admin_id"], ["admin_user.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("out_refund_no"),
        sa.UniqueConstraint("wechat_refund_id"),
    )
    op.create_index("ix_renshe_refund_request_order_id", "renshe_refund_request", ["order_id"])
    op.create_index(
        "ix_renshe_refund_status_due", "renshe_refund_request", ["status", "due_at"]
    )
    op.create_index(
        "ix_renshe_refund_status_updated",
        "renshe_refund_request",
        ["status", "updated_at"],
    )
    op.create_index(
        "uq_renshe_refund_active_order",
        "renshe_refund_request",
        ["order_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('requested', 'approved', 'processing', 'failed')"
        ),
    )
    op.create_index(
        "uq_renshe_refund_succeeded_order",
        "renshe_refund_request",
        ["order_id"],
        unique=True,
        postgresql_where=sa.text("status = 'succeeded'"),
    )

    op.create_table(
        "renshe_export_job",
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("generation_no", sa.Integer(), nullable=False),
        sa.Column("requested_by_admin_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column("candidate_total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("candidate_processed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("volume_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_renshe_export_job_status",
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["plan.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["requested_by_admin_id"], ["admin_user.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "generation_no", name="uq_renshe_export_generation"),
    )
    op.create_index(
        "ix_renshe_export_plan_status", "renshe_export_job", ["plan_id", "status"]
    )

    op.create_table(
        "renshe_export_volume",
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("volume_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column("candidate_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_renshe_export_volume_status",
        ),
        sa.CheckConstraint(
            "size_bytes IS NULL OR size_bytes <= 10737418240",
            name="ck_renshe_export_volume_max_size",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["renshe_export_job.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "volume_no", name="uq_renshe_export_volume_no"),
        sa.UniqueConstraint("storage_key"),
    )

    op.create_table(
        "renshe_export_item",
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("volume_id", sa.Integer(), nullable=True),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column("estimated_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_renshe_export_item_status",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["renshe_export_job.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["volume_id"], ["renshe_export_volume.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["application_id"], ["renshe_application.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["version_id"], ["renshe_application_version.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id", "application_id", name="uq_renshe_export_item_application"
        ),
    )
    op.create_index(
        "ix_renshe_export_item_job_status", "renshe_export_item", ["job_id", "status"]
    )

    op.create_table(
        "renshe_cleanup_run",
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("run_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="scheduled", nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paused_reason", sa.String(length=128), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rebase_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "status IN ('scheduled', 'running', 'paused', 'succeeded', 'failed')",
            name="ck_renshe_cleanup_status",
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["plan.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "run_no", name="uq_renshe_cleanup_plan_run"),
    )
    op.create_index(
        "ix_renshe_cleanup_status_due", "renshe_cleanup_run", ["status", "due_at"]
    )

    op.create_table(
        "renshe_audit_log",
        sa.Column("actor_type", sa.String(length=16), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("object_type", sa.String(length=32), nullable=False),
        sa.Column("object_id", sa.Integer(), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=True),
        sa.Column("version_id", sa.Integer(), nullable=True),
        sa.Column("material_id", sa.Integer(), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=True),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "actor_type IN ('user', 'admin', 'system')",
            name="ck_renshe_audit_actor_type",
        ),
        sa.CheckConstraint(
            "result IN ('succeeded', 'failed')", name="ck_renshe_audit_result"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_renshe_audit_log_action", "renshe_audit_log", ["action"])
    op.create_index(
        "ix_renshe_audit_object", "renshe_audit_log", ["object_type", "object_id"]
    )
    op.create_index(
        "ix_renshe_audit_actor", "renshe_audit_log", ["actor_type", "actor_id"]
    )
    op.create_index(
        "ix_renshe_audit_application_created",
        "renshe_audit_log",
        ["application_id", "created_at"],
    )
    op.create_index(
        "ix_renshe_audit_result_created",
        "renshe_audit_log",
        ["result", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_renshe_audit_result_created", table_name="renshe_audit_log")
    op.drop_index(
        "ix_renshe_audit_application_created", table_name="renshe_audit_log"
    )
    op.drop_index("ix_renshe_audit_actor", table_name="renshe_audit_log")
    op.drop_index("ix_renshe_audit_object", table_name="renshe_audit_log")
    op.drop_index("ix_renshe_audit_log_action", table_name="renshe_audit_log")
    op.drop_table("renshe_audit_log")

    op.drop_index("ix_renshe_cleanup_status_due", table_name="renshe_cleanup_run")
    op.drop_table("renshe_cleanup_run")

    op.drop_index("ix_renshe_export_item_job_status", table_name="renshe_export_item")
    op.drop_table("renshe_export_item")
    op.drop_table("renshe_export_volume")
    op.drop_index("ix_renshe_export_plan_status", table_name="renshe_export_job")
    op.drop_table("renshe_export_job")

    op.drop_index("uq_renshe_refund_succeeded_order", table_name="renshe_refund_request")
    op.drop_index("uq_renshe_refund_active_order", table_name="renshe_refund_request")
    op.drop_index("ix_renshe_refund_status_updated", table_name="renshe_refund_request")
    op.drop_index("ix_renshe_refund_status_due", table_name="renshe_refund_request")
    op.drop_index("ix_renshe_refund_request_order_id", table_name="renshe_refund_request")
    op.drop_table("renshe_refund_request")

    op.drop_index("uq_order_active_application", table_name="order")
    op.drop_index("ix_order_application_id", table_name="order")
    op.drop_constraint(
        "fk_order_application_id_renshe_application", "order", type_="foreignkey"
    )
    op.drop_column("order", "application_id")

    op.drop_table("renshe_review_correction")
    op.drop_index("ix_renshe_review_application_stage", table_name="renshe_review")
    op.drop_table("renshe_review")

    op.drop_index("uq_renshe_material_version_kind", table_name="renshe_material")
    op.drop_index("ix_renshe_material_application", table_name="renshe_material")
    op.drop_index("ix_renshe_material_version_id", table_name="renshe_material")
    op.drop_index("ix_renshe_material_application_id", table_name="renshe_material")
    op.drop_table("renshe_material")

    op.drop_constraint(
        "fk_renshe_application_current_version",
        "renshe_application",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_renshe_version_application", table_name="renshe_application_version"
    )
    op.drop_index(
        "ix_renshe_application_version_application_id",
        table_name="renshe_application_version",
    )
    op.drop_table("renshe_application_version")

    op.drop_index(
        "uq_renshe_application_active_user_plan", table_name="renshe_application"
    )
    op.drop_index("ix_renshe_application_user_status", table_name="renshe_application")
    op.drop_index("ix_renshe_application_plan_status", table_name="renshe_application")
    op.drop_index("ix_renshe_application_status", table_name="renshe_application")
    op.drop_index("ix_renshe_application_user_id", table_name="renshe_application")
    op.drop_index("ix_renshe_application_plan_id", table_name="renshe_application")
    op.drop_table("renshe_application")

    op.drop_index("ix_deleted_identity_hash_id_card_hash", table_name="deleted_identity_hash")
    op.drop_index("ix_deleted_identity_hash_former_user_id", table_name="deleted_identity_hash")
    op.drop_table("deleted_identity_hash")

    op.drop_column("user_student", "enrollment_date")
    op.drop_index("uq_user_realname_active_id_card_hash", table_name="user_realname")
    op.drop_index("ix_user_realname_id_card_hash", table_name="user_realname")
    op.drop_column("user_realname", "active_id_card_hash")
    op.drop_column("user_realname", "id_card_hash")

    op.drop_constraint("ck_plan_sort_order_nonnegative", "plan", type_="check")
    op.drop_constraint("ck_plan_capacity_nonnegative", "plan", type_="check")
    op.drop_constraint("ck_plan_price_nonnegative", "plan", type_="check")
    op.drop_constraint("ck_plan_status", "plan", type_="check")
    for column in (
        "cleanup_due_at",
        "finalized_at",
        "cancelled_at",
        "registration_closed_at",
        "published_at",
        "sort_order",
        "contact_phone",
        "contact_name",
        "description",
        "exam_location",
        "price_cents",
        "application_type",
        "exam_type",
        "skill_level",
        "occupation_code",
        "occupation_name",
    ):
        op.drop_column("plan", column)
    # The pre-rsh001 schema has no separate registration_closed/finalized
    # states.  Normalize those values before restoring its narrower check so
    # a downgrade of a populated test/production-equivalent database does not
    # fail while re-adding ``ck_plan_status``.
    op.execute(
        "UPDATE plan SET status = 'published' WHERE status = 'registration_closed'"
    )
    op.execute(
        "UPDATE plan SET status = 'archived' WHERE status = 'finalized'"
    )
    op.create_check_constraint(
        "ck_plan_status",
        "plan",
        "status IN ('draft', 'published', 'archived', 'cancelled')",
    )

    op.drop_constraint("ck_admin_user_role", "admin_user", type_="check")
    op.execute("UPDATE admin_user SET role = 'content_editor' WHERE role = 'admin'")
    op.alter_column(
        "admin_user",
        "role",
        existing_type=sa.String(length=32),
        nullable=False,
        server_default="content_editor",
    )
    op.create_check_constraint(
        "ck_admin_user_role",
        "admin_user",
        "role = ANY (ARRAY['super_admin', 'content_editor', "
        "'customer_service', 'finance', 'auditor'])",
    )
