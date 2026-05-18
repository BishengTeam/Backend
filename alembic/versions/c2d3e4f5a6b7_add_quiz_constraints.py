"""add quiz constraints

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-05-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, Sequence[str], None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_quiz_record_user_question",
        "quiz_record",
        ["user_id", "question_id"],
        unique=True,
    )
    op.create_index(
        "uq_quiz_checkin_user_date",
        "quiz_checkin",
        ["user_id", "checkin_date"],
        unique=True,
    )
    op.create_check_constraint(
        "ck_quiz_question_type",
        "quiz_question",
        "question_type IN ('single_choice', 'multiple_choice', 'judge')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_quiz_question_type", "quiz_question", type_="check")
    op.drop_index("uq_quiz_checkin_user_date", table_name="quiz_checkin")
    op.drop_index("uq_quiz_record_user_question", table_name="quiz_record")
