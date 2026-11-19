"""Constrain exam_booking.vendor to known vendors.

Revision ID: exam006
Revises: exam005
"""
from alembic import op


revision = "exam006"
down_revision = "exam005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_exam_booking_vendor",
        "exam_booking",
        "vendor IN ('h3c','sangfor')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_exam_booking_vendor", "exam_booking", type_="check"
    )
