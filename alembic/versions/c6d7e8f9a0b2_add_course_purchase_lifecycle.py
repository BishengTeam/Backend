"""add course purchase lifecycle

Revision ID: c6d7e8f9a0b2
Revises: d5f6a7b8c9d0
Create Date: 2026-07-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c6d7e8f9a0b2"
down_revision: Union[str, Sequence[str], None] = "d5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "course_enrollment",
        sa.Column("order_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "course_enrollment",
        sa.Column("access_granted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "course_enrollment",
        sa.Column("access_revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_course_enrollment_order_id_order",
        "course_enrollment",
        "order",
        ["order_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Existing enrollments retain access when their state supports it. Invalid
    # legacy combinations are revoked so the new invariant can be enforced.
    op.execute(
        """
        UPDATE course_enrollment
        SET access_granted_at = COALESCE(access_granted_at, created_at)
        WHERE learning_access IS TRUE
          AND status IN ('enrolled', 'completed')
        """
    )
    op.execute(
        """
        UPDATE course_enrollment
        SET learning_access = FALSE,
            access_revoked_at = COALESCE(access_revoked_at, updated_at, CURRENT_TIMESTAMP)
        WHERE learning_access IS TRUE
          AND status NOT IN ('enrolled', 'completed')
        """
    )

    # Resolve historical duplicate active rows deterministically before adding
    # the partial unique index. One access-bearing row remains active.
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY user_id, course_id
                       ORDER BY learning_access DESC,
                                CASE status
                                    WHEN 'completed' THEN 0
                                    WHEN 'enrolled' THEN 1
                                    ELSE 2
                                END,
                                created_at ASC,
                                id ASC
                   ) AS row_number
            FROM course_enrollment
            WHERE status IN ('pending_payment', 'enrolled', 'completed')
        )
        UPDATE course_enrollment AS enrollment
        SET status = 'expired',
            learning_access = FALSE,
            access_revoked_at = COALESCE(
                enrollment.access_revoked_at,
                enrollment.updated_at,
                CURRENT_TIMESTAMP
            )
        FROM ranked
        WHERE enrollment.id = ranked.id
          AND ranked.row_number > 1
        """
    )

    op.alter_column(
        "course_enrollment",
        "status",
        existing_type=sa.String(length=16),
        nullable=False,
        server_default="pending_payment",
    )
    op.alter_column(
        "course_enrollment",
        "learning_access",
        existing_type=sa.Boolean(),
        nullable=False,
        server_default=sa.text("false"),
    )
    op.create_check_constraint(
        "ck_course_enrollment_status",
        "course_enrollment",
        "status IN ('pending_payment', 'enrolled', 'completed', "
        "'refunded', 'cancelled', 'expired')",
    )
    op.create_check_constraint(
        "ck_course_enrollment_learning_access",
        "course_enrollment",
        "learning_access IS FALSE OR status IN ('enrolled', 'completed')",
    )
    op.create_index(
        "uq_course_enrollment_order_id",
        "course_enrollment",
        ["order_id"],
        unique=True,
    )
    op.create_index(
        "uq_course_enrollment_active_user_course",
        "course_enrollment",
        ["user_id", "course_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('pending_payment', 'enrolled', 'completed')"
        ),
    )
    op.create_index(
        "ix_course_enrollment_status",
        "course_enrollment",
        ["status"],
        unique=False,
    )
    op.create_check_constraint(
        "ck_course_price_nonnegative",
        "course",
        "price >= 0",
    )

    op.create_table(
        "course_asset",
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("asset_type", sa.String(length=32), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_preview", sa.Boolean(), server_default=sa.text("false"), nullable=False),
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
        sa.CheckConstraint(
            "sort_order >= 0",
            name="ck_course_asset_sort_order_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["course.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index(
        "ix_course_asset_course_sort",
        "course_asset",
        ["course_id", "sort_order", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_course_asset_course_sort", table_name="course_asset")
    op.drop_table("course_asset")

    op.drop_constraint("ck_course_price_nonnegative", "course", type_="check")
    op.drop_index("ix_course_enrollment_status", table_name="course_enrollment")
    op.drop_index(
        "uq_course_enrollment_active_user_course",
        table_name="course_enrollment",
    )
    op.drop_index("uq_course_enrollment_order_id", table_name="course_enrollment")
    op.drop_constraint(
        "ck_course_enrollment_learning_access",
        "course_enrollment",
        type_="check",
    )
    op.drop_constraint(
        "ck_course_enrollment_status",
        "course_enrollment",
        type_="check",
    )
    op.alter_column(
        "course_enrollment",
        "learning_access",
        existing_type=sa.Boolean(),
        nullable=False,
        server_default=sa.text("true"),
    )
    op.alter_column(
        "course_enrollment",
        "status",
        existing_type=sa.String(length=16),
        nullable=False,
        server_default="enrolled",
    )
    op.drop_constraint(
        "fk_course_enrollment_order_id_order",
        "course_enrollment",
        type_="foreignkey",
    )
    op.drop_column("course_enrollment", "access_revoked_at")
    op.drop_column("course_enrollment", "access_granted_at")
    op.drop_column("course_enrollment", "order_id")
