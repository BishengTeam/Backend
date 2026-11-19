"""Make exam bookings vendor-aware (H3C registration or Sangfor order).

Revision ID: exam004
Revises: u11
"""
import os

from alembic import op
import sqlalchemy as sa


revision = "exam004"
down_revision = "u11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "exam_booking",
        sa.Column("vendor", sa.String(length=32), nullable=False, server_default="h3c"),
    )
    # Sangfor reservations point at order.id instead of h3c_registration.id, so
    # the single-table foreign key cannot be kept.
    op.drop_constraint(
        "exam_booking_registration_id_fkey", "exam_booking", type_="foreignkey"
    )
    op.drop_constraint("uq_exam_booking_once", "exam_booking", type_="unique")
    op.create_index(
        "uq_exam_booking_once",
        "exam_booking",
        ["vendor", "registration_id", "schedule_id"],
        unique=True,
    )
    op.create_index(
        "ix_exam_booking_vendor_registration",
        "exam_booking",
        ["vendor", "registration_id"],
    )


def downgrade() -> None:
    if not op.get_context().as_sql:
        bind = op.get_bind()
        non_h3c = bind.execute(
            sa.text("SELECT count(*) FROM exam_booking WHERE vendor <> 'h3c'")
        ).scalar_one()
        if non_h3c and os.getenv("ALLOW_DESTRUCTIVE_EXAM_DOWNGRADE") != "1":
            raise RuntimeError(
                "exam004 downgrade would delete "
                f"{non_h3c} non-H3C booking rows; export a backup and set "
                "ALLOW_DESTRUCTIVE_EXAM_DOWNGRADE=1 to proceed"
            )
    op.drop_index("ix_exam_booking_vendor_registration", table_name="exam_booking")
    op.drop_index("uq_exam_booking_once", table_name="exam_booking")
    # Non-H3C rows cannot satisfy the restored foreign key.
    op.execute("DELETE FROM exam_booking WHERE vendor <> 'h3c'")
    op.create_unique_constraint(
        "uq_exam_booking_once",
        "exam_booking",
        ["registration_id", "schedule_id"],
    )
    op.create_foreign_key(
        "exam_booking_registration_id_fkey",
        "exam_booking",
        "h3c_registration",
        ["registration_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_column("exam_booking", "vendor")
