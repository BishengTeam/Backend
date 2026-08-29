"""add wrong-book mastery counters

Revision ID: quiz010
Revises: cp004_cert_product_catalog
Create Date: 2026-08-29 10:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "quiz010"
down_revision: str | Sequence[str] | None = "cp004_cert_product_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "quiz_wrong_item",
        sa.Column(
            "wrong_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.add_column(
        "quiz_wrong_item",
        sa.Column(
            "consecutive_correct_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.create_check_constraint(
        "ck_quiz_wrong_item_wrong_count",
        "quiz_wrong_item",
        "wrong_count >= 0",
    )
    op.create_check_constraint(
        "ck_quiz_wrong_item_consecutive_correct_count",
        "quiz_wrong_item",
        "consecutive_correct_count >= 0",
    )

    op.execute("""
        UPDATE quiz_wrong_item AS item
        SET wrong_count = GREATEST(
            1,
            COALESCE(practice.wrong_count, 0) + COALESCE(exam.wrong_count, 0)
        )
        FROM (
            SELECT wrong_item.id, COUNT(attempt.id) AS wrong_count
            FROM quiz_wrong_item AS wrong_item
            JOIN quiz_practice_attempt AS attempt
              ON attempt.user_id = wrong_item.user_id
             AND attempt.is_first_attempt = TRUE
             AND attempt.is_correct = FALSE
             AND attempt.submitted_at >= wrong_item.first_wrong_at
            JOIN quiz_practice_session_question AS session_question
              ON session_question.id = attempt.session_question_id
             AND session_question.question_id = wrong_item.question_id
            GROUP BY wrong_item.id
        ) AS practice
        LEFT JOIN (
            SELECT wrong_item.id, COUNT(exam_answer.id) AS wrong_count
            FROM quiz_wrong_item AS wrong_item
            JOIN quiz_exam AS exam
              ON exam.user_id = wrong_item.user_id
             AND exam.status IN ('completed', 'timed_out')
            JOIN quiz_exam_question AS exam_question
              ON exam_question.exam_id = exam.id
             AND exam_question.question_id = wrong_item.question_id
            JOIN quiz_exam_answer AS exam_answer
              ON exam_answer.exam_id = exam_question.exam_id
             AND exam_answer.exam_question_id = exam_question.id
             AND exam_answer.is_correct = FALSE
             AND exam_answer.saved_at >= wrong_item.first_wrong_at
            GROUP BY wrong_item.id
        ) AS exam
          ON exam.id = practice.id
        LEFT JOIN quiz_wrong_item AS wrong_item
          ON wrong_item.id = practice.id
        WHERE item.id = practice.id
          AND item.status = 'active'
    """)
    op.execute("""
        UPDATE quiz_wrong_item
        SET wrong_count = 0,
            consecutive_correct_count = 0
        WHERE status = 'cleared'
    """)


def downgrade() -> None:
    op.drop_constraint(
        "ck_quiz_wrong_item_consecutive_correct_count",
        "quiz_wrong_item",
        type_="check",
    )
    op.drop_constraint(
        "ck_quiz_wrong_item_wrong_count",
        "quiz_wrong_item",
        type_="check",
    )
    op.drop_column("quiz_wrong_item", "consecutive_correct_count")
    op.drop_column("quiz_wrong_item", "wrong_count")
