"""add quiz import confirmation workflow and row-level errors

Revision ID: quiz003
Revises: plan003
Create Date: 2026-08-13 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "quiz003"
down_revision: str | Sequence[str] | None = "plan003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_quiz_import_status",
        "quiz_import_job",
        type_="check",
    )
    op.alter_column(
        "quiz_import_job",
        "status",
        existing_type=sa.String(length=24),
        type_=sa.String(length=32),
        existing_nullable=False,
        existing_server_default="queued",
    )
    op.create_check_constraint(
        "ck_quiz_import_status",
        "quiz_import_job",
        "status IN ('queued', 'validating', 'validation_failed', 'importing', "
        "'awaiting_category_confirmation', 'succeeded', 'failed', 'cancelled', "
        "'expired')",
    )
    op.add_column(
        "quiz_import_job",
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "quiz_import_job",
        sa.Column("validation_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "quiz_import_job",
        sa.Column("impact_version", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "quiz_import_job",
        sa.Column("category_impact", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "quiz_import_job",
        sa.Column("missing_category_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "quiz_import_job",
        sa.Column("affected_question_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "quiz_import_job",
        sa.Column("confirmed_by", sa.Integer(), nullable=True),
    )
    op.add_column(
        "quiz_import_job",
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "quiz_import_job",
        sa.Column("execution_protected_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_quiz_import_lock_version",
        "quiz_import_job",
        "lock_version >= 1",
    )
    op.create_check_constraint(
        "ck_quiz_import_validation_version",
        "quiz_import_job",
        "validation_version >= 0",
    )
    op.create_check_constraint(
        "ck_quiz_import_category_counts",
        "quiz_import_job",
        "missing_category_count BETWEEN 0 AND 500 "
        "AND affected_question_count BETWEEN 0 AND 5000",
    )
    op.create_foreign_key(
        "fk_quiz_import_job_confirmed_by_admin",
        "quiz_import_job",
        "admin_user",
        ["confirmed_by"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "quiz_import_error",
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("validation_version", sa.Integer(), nullable=False),
        sa.Column("row", sa.Integer(), nullable=True),
        sa.Column("question_index", sa.Integer(), nullable=True),
        sa.Column("field", sa.String(length=128), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=False),
        sa.Column("message", sa.String(length=1024), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            '"row" IS NULL OR "row" >= 1',
            name="ck_quiz_import_error_row",
        ),
        sa.CheckConstraint(
            "question_index IS NULL OR question_index >= 1",
            name="ck_quiz_import_error_question_index",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["quiz_import_job.id"],
            name="fk_quiz_import_error_job",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_quiz_import_error_page",
        "quiz_import_error",
        ["job_id", "validation_version", "id"],
    )
    op.create_index(
        "ix_quiz_import_error_field",
        "quiz_import_error",
        ["job_id", "validation_version", "field", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_quiz_import_error_field", table_name="quiz_import_error")
    op.drop_index("ix_quiz_import_error_page", table_name="quiz_import_error")
    op.drop_table("quiz_import_error")

    op.drop_constraint(
        "fk_quiz_import_job_confirmed_by_admin",
        "quiz_import_job",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_quiz_import_category_counts",
        "quiz_import_job",
        type_="check",
    )
    op.drop_constraint(
        "ck_quiz_import_validation_version",
        "quiz_import_job",
        type_="check",
    )
    op.drop_constraint(
        "ck_quiz_import_lock_version",
        "quiz_import_job",
        type_="check",
    )
    for column in (
        "execution_protected_until",
        "confirmed_at",
        "confirmed_by",
        "affected_question_count",
        "missing_category_count",
        "category_impact",
        "impact_version",
        "validation_version",
        "lock_version",
    ):
        op.drop_column("quiz_import_job", column)

    op.execute(
        "UPDATE quiz_import_job SET status = 'failed' "
        "WHERE status IN ('awaiting_category_confirmation', 'cancelled', 'expired')"
    )
    op.drop_constraint(
        "ck_quiz_import_status",
        "quiz_import_job",
        type_="check",
    )
    op.alter_column(
        "quiz_import_job",
        "status",
        existing_type=sa.String(length=32),
        type_=sa.String(length=24),
        existing_nullable=False,
        existing_server_default="queued",
    )
    op.create_check_constraint(
        "ck_quiz_import_status",
        "quiz_import_job",
        "status IN ('queued', 'validating', 'validation_failed', 'importing', "
        "'succeeded', 'failed')",
    )
