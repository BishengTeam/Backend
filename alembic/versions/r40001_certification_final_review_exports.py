"""Certification export versions and final review

Revision ID: r40001
Revises: r30001
Create Date: 2026-10-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "r40001"
down_revision: Union[str, Sequence[str] | None] = "r30001"
branch_labels: Union[str, Sequence[str] | None] = None
depends_on: Union[str, Sequence[str] | None] = None


def _final_review_table(table: str, registration_table: str, export_table: str, export_item_table: str, prefix: str) -> None:
    op.create_table(
        table,
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "registration_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{registration_table}.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "export_job_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{export_table}.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "export_item_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{export_item_table}.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("registration_version_id", sa.BigInteger(), nullable=False),
        sa.Column("material_version_ids", JSONB(), nullable=True),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("reason_detail", sa.Text(), nullable=True),
        sa.Column(
            "reviewer_admin_id",
            sa.Integer(),
            sa.ForeignKey("admin_user.id"),
            nullable=False,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("registration_id", name=f"uq_{prefix}_final_review_registration"),
        sa.CheckConstraint("decision IN ('approved', 'rejected')", name=f"ck_{prefix}_final_review_decision"),
    )
    op.create_index(f"ix_{prefix}_final_review_job", table, ["export_job_id", "registration_id"])


def upgrade() -> None:
    op.add_column("h3c_export_job", sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("h3c_export_job", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("nisp_export_job", sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("nisp_export_job", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("h3c_export_item", sa.Column("registration_version_id", sa.BigInteger(), nullable=True))
    op.add_column("h3c_export_item", sa.Column("candidate_snapshot", JSONB(), nullable=True))
    op.add_column("h3c_export_item", sa.Column("material_versions", JSONB(), nullable=True))
    op.add_column("h3c_export_item", sa.Column("is_valid", sa.Boolean(), nullable=False, server_default=sa.text("true")))
    op.add_column("h3c_export_item", sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("h3c_export_item", sa.Column("invalidated_reason", sa.String(length=128), nullable=True))

    _final_review_table(
        "h3c_final_review", "h3c_registration", "h3c_export_job", "h3c_export_item", "h3c"
    )

    op.create_table(
        "nisp_export_item",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.BigInteger(), sa.ForeignKey("nisp_export_job.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("registration_id", sa.BigInteger(), sa.ForeignKey("nisp_registration.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("registration_version_id", sa.BigInteger(), nullable=True),
        sa.Column("candidate_snapshot", JSONB(), nullable=True),
        sa.Column("material_versions", JSONB(), nullable=True),
        sa.Column("is_valid", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_reason", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "registration_id", name="uq_nisp_export_item"),
    )
    op.create_index("ix_nisp_export_item_registration", "nisp_export_item", ["registration_id", "id"])
    _final_review_table(
        "nisp_final_review", "nisp_registration", "nisp_export_job", "nisp_export_item", "nisp"
    )

    op.execute("""
        UPDATE h3c_export_item item
        SET registration_version_id = version.id,
            candidate_snapshot = version.candidate_snapshot,
            material_versions = version.material_versions
        FROM h3c_registration_version version
        WHERE version.registration_id = item.registration_id
          AND version.is_current = true
    """)
    op.drop_constraint("ck_h3c_registration_status", "h3c_registration", type_="check")
    op.create_check_constraint(
        "ck_h3c_registration_status", "h3c_registration",
        "status IN ('pending_payment', 'pending_review', 'rejected_awaiting_resubmission', 'pending_refund_confirmation', 'refund_processing', 'approved', 'final_approved', 'refunded_closed', 'cancelled')",
    )
    op.drop_index("uq_h3c_registration_active_batch_idcard", table_name="h3c_registration")
    op.create_index(
        "uq_h3c_registration_active_batch_idcard", "h3c_registration",
        ["batch_id", "candidate_idcard"], unique=True,
        postgresql_where=sa.text("status IN ('pending_payment', 'pending_review', 'rejected_awaiting_resubmission', 'pending_refund_confirmation', 'refund_processing', 'approved', 'final_approved')"),
    )

    op.drop_constraint("ck_nisp_registration_status", "nisp_registration", type_="check")
    op.create_check_constraint(
        "ck_nisp_registration_status", "nisp_registration",
        "status IN ('pending_payment', 'pending_review', 'rejected_awaiting_resubmission', 'pending_refund_confirmation', 'refund_processing', 'approved', 'final_approved', 'refunded_closed', 'cancelled')",
    )
    op.drop_index("uq_nisp_registration_active_batch_idcard", table_name="nisp_registration")
    op.create_index(
        "uq_nisp_registration_active_batch_idcard", "nisp_registration",
        ["batch_id", "candidate_idcard"], unique=True,
        postgresql_where=sa.text("status IN ('pending_payment', 'pending_review', 'rejected_awaiting_resubmission', 'pending_refund_confirmation', 'refund_processing', 'approved', 'final_approved')"),
    )


def downgrade() -> None:
    op.drop_table("nisp_final_review")
    op.drop_table("nisp_export_item")
    op.drop_table("h3c_final_review")
    for column in [
        "invalidated_reason", "invalidated_at", "is_valid", "material_versions",
        "candidate_snapshot", "registration_version_id",
    ]:
        op.drop_column("h3c_export_item", column)
    for table in ["h3c_export_job", "nisp_export_job"]:
        op.drop_column(table, "lease_expires_at")
        op.drop_column(table, "retry_count")

    op.drop_index("uq_nisp_registration_active_batch_idcard", table_name="nisp_registration")
    op.create_index(
        "uq_nisp_registration_active_batch_idcard", "nisp_registration",
        ["batch_id", "candidate_idcard"], unique=True,
        postgresql_where=sa.text("status IN ('pending_payment', 'pending_review', 'rejected_awaiting_resubmission', 'pending_refund_confirmation', 'refund_processing', 'approved')"),
    )
    op.drop_constraint("ck_nisp_registration_status", "nisp_registration", type_="check")
    op.create_check_constraint(
        "ck_nisp_registration_status", "nisp_registration",
        "status IN ('pending_payment', 'pending_review', 'rejected_awaiting_resubmission', 'pending_refund_confirmation', 'refund_processing', 'approved', 'refunded_closed', 'cancelled')",
    )
    op.drop_index("uq_h3c_registration_active_batch_idcard", table_name="h3c_registration")
    op.create_index(
        "uq_h3c_registration_active_batch_idcard", "h3c_registration",
        ["batch_id", "candidate_idcard"], unique=True,
        postgresql_where=sa.text("status IN ('pending_payment', 'pending_review', 'rejected_awaiting_resubmission', 'pending_refund_confirmation', 'refund_processing', 'approved')"),
    )
    op.drop_constraint("ck_h3c_registration_status", "h3c_registration", type_="check")
    op.create_check_constraint(
        "ck_h3c_registration_status", "h3c_registration",
        "status IN ('pending_payment', 'pending_review', 'rejected_awaiting_resubmission', 'pending_refund_confirmation', 'refund_processing', 'approved', 'refunded_closed', 'cancelled')",
    )
