"""Make exam bookings vendor-aware (H3C registration or Sangfor order).

Revision ID: exam004
Revises: adm002
"""
from alembic import op
import sqlalchemy as sa


revision = "exam004"
down_revision = "adm002"
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
