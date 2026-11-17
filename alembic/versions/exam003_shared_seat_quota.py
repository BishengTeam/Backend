"""Convert H3C seat quotas to shared time-slot pools.

Revision ID: exam003
Revises: exam002
"""
from alembic import op
import sqlalchemy as sa


revision = "exam003"
down_revision = "exam002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "exam_seat_quota",
        "subject_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.drop_constraint(
        "uq_exam_seat_quota",
        "exam_seat_quota",
        type_="unique",
    )

    # Existing subject-specific pools are preserved for rollback. Create one
    # conservative shared pool per slot using the largest legacy per-subject
    # capacity, so no previously advertised subject capacity becomes unlimited.
    op.execute(
        """
        INSERT INTO exam_seat_quota
            (schedule_id, subject_id, slot_id, total_seats)
        SELECT schedule_id, NULL, slot_id, MAX(total_seats)
        FROM exam_seat_quota
        GROUP BY schedule_id, slot_id
        """
    )

    op.create_index(
        "uq_exam_seat_quota_shared",
        "exam_seat_quota",
        ["schedule_id", "slot_id"],
        unique=True,
        postgresql_where=sa.text("subject_id IS NULL"),
    )
    op.create_index(
        "uq_exam_seat_quota_subject",
        "exam_seat_quota",
        ["schedule_id", "subject_id", "slot_id"],
        unique=True,
        postgresql_where=sa.text("subject_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_exam_seat_quota_subject",
        table_name="exam_seat_quota",
    )
    op.drop_index(
        "uq_exam_seat_quota_shared",
        table_name="exam_seat_quota",
    )
    # Propagate the current shared pool back to every legacy subject row before
    # dropping it, so a downgrade does not lose newly configured capacity.
    op.execute(
        """
        UPDATE exam_seat_quota AS legacy
        SET total_seats = shared.total_seats
        FROM exam_seat_quota AS shared
        WHERE legacy.subject_id IS NOT NULL
          AND shared.subject_id IS NULL
          AND legacy.schedule_id = shared.schedule_id
          AND legacy.slot_id = shared.slot_id
        """
    )
    op.execute(
        """
        INSERT INTO exam_seat_quota
            (schedule_id, subject_id, slot_id, total_seats)
        SELECT shared.schedule_id, subject.id, shared.slot_id, shared.total_seats
        FROM exam_seat_quota AS shared
        JOIN exam_schedule_subject AS subject
          ON subject.schedule_id = shared.schedule_id
        WHERE shared.subject_id IS NULL
          AND NOT EXISTS (
              SELECT 1
              FROM exam_seat_quota AS legacy
              WHERE legacy.schedule_id = shared.schedule_id
                AND legacy.subject_id = subject.id
                AND legacy.slot_id = shared.slot_id
          )
        """
    )
    op.execute("DELETE FROM exam_seat_quota WHERE subject_id IS NULL")
    op.alter_column(
        "exam_seat_quota",
        "subject_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_exam_seat_quota",
        "exam_seat_quota",
        ["schedule_id", "subject_id", "slot_id"],
    )
