"""Certification correction requests and immutable info versions

Revision ID: r30001
Revises: pay001
Create Date: 2026-10-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "r30001"
down_revision: Union[str, Sequence[str] | None] = "pay001"
branch_labels: Union[str, Sequence[str] | None] = None
depends_on: Union[str, Sequence[str] | None] = None


def _create_version_table(table: str, registration_table: str, prefix: str) -> None:
    op.create_table(
        table,
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "registration_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{registration_table}.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("candidate_snapshot", JSONB(), nullable=False),
        sa.Column("material_versions", JSONB(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "is_current",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("registration_id", "version_no", name=f"uq_{prefix}_version_no"),
        sa.CheckConstraint("version_no > 0", name=f"ck_{prefix}_version_no_positive"),
        sa.CheckConstraint(
            "source IN ('initial', 'user_resubmission')",
            name=f"ck_{prefix}_version_source",
        ),
    )
    op.create_index(
        f"ix_{prefix}_version_current",
        table,
        ["registration_id", "is_current"],
    )


def _create_correction_table(table: str, registration_table: str, prefix: str) -> None:
    op.create_table(
        table,
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "registration_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{registration_table}.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("review_id", sa.BigInteger(), nullable=True),
        sa.Column("allowed_fields", JSONB(), nullable=False),
        sa.Column("allowed_material_types", JSONB(), nullable=True),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("reason_detail", sa.Text(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_by_admin_id",
            sa.Integer(),
            sa.ForeignKey("admin_user.id"),
            nullable=True,
        ),
        sa.Column("submitted_version_id", sa.BigInteger(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('pending', 'submitted', 'expired', 'cancelled')",
            name=f"ck_{prefix}_correction_status",
        ),
    )
    op.create_index(
        f"ix_{prefix}_correction_registration",
        table,
        ["registration_id"],
    )
    op.create_index(
        f"uq_{prefix}_correction_pending",
        table,
        ["registration_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def upgrade() -> None:
    _create_version_table("h3c_registration_version", "h3c_registration", "h3c")
    _create_correction_table("h3c_correction_request", "h3c_registration", "h3c")
    _create_version_table("nisp_registration_version", "nisp_registration", "nisp")
    _create_correction_table("nisp_correction_request", "nisp_registration", "nisp")

    op.execute(
        """
        INSERT INTO h3c_registration_version
            (registration_id, version_no, candidate_snapshot, source,
             submitted_at, is_current, created_at, updated_at)
        SELECT id, 1, candidate_snapshot, 'initial',
               COALESCE(created_at, now()), true, now(), now()
        FROM h3c_registration
        """
    )
    op.drop_constraint("ck_h3c_review_decision", "h3c_review", type_="check")
    op.create_check_constraint(
        "ck_h3c_review_decision",
        "h3c_review",
        "decision IN ('approved', 'rejected', 'rejected_refund')",
    )
    op.drop_constraint("ck_nisp_review_decision", "nisp_review", type_="check")
    op.create_check_constraint(
        "ck_nisp_review_decision",
        "nisp_review",
        "decision IN ('approved', 'rejected', 'rejected_refund')",
    )
    op.execute(
        """
        INSERT INTO nisp_registration_version
            (registration_id, version_no, candidate_snapshot, source,
             submitted_at, is_current, created_at, updated_at)
        SELECT id, 1, candidate_snapshot, 'initial',
               COALESCE(created_at, now()), true, now(), now()
        FROM nisp_registration
        """
    )


def downgrade() -> None:
    op.drop_constraint("ck_nisp_review_decision", "nisp_review", type_="check")
    op.create_check_constraint(
        "ck_nisp_review_decision", "nisp_review", "decision IN ('approved', 'rejected')"
    )
    op.drop_constraint("ck_h3c_review_decision", "h3c_review", type_="check")
    op.create_check_constraint(
        "ck_h3c_review_decision", "h3c_review", "decision IN ('approved', 'rejected')"
    )
    op.drop_table("nisp_correction_request")
    op.drop_table("nisp_registration_version")
    op.drop_table("h3c_correction_request")
    op.drop_table("h3c_registration_version")
