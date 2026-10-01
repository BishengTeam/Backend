"""quiz manual check-in without practice

Revision ID: quiz014_manual_checkin
Revises: nisp001
"""
from alembic import op
from collections.abc import Sequence

import sqlalchemy as sa


revision: str = 'quiz014_manual_checkin'
down_revision: str | Sequence[str] = 'nisp001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 手动打卡：first_attempt_id 可空（NULL = 手动打卡，无练习作答），
    # questions_completed 允许为 0（未练习手动打卡）。
    # uq_quiz_checkin_first_attempt 保留：PostgreSQL 唯一约束允许多个 NULL，
    # 多天手动打卡互不冲突，自动打卡的作答仍全局唯一。
    op.alter_column(
        'quiz_checkin',
        'first_attempt_id',
        existing_type=sa.BigInteger(),
        nullable=True,
    )
    op.drop_constraint(
        'ck_quiz_checkin_questions_completed', 'quiz_checkin', type_='check'
    )
    op.create_check_constraint(
        'ck_quiz_checkin_questions_completed',
        'quiz_checkin',
        'questions_completed >= 0',
    )


def downgrade() -> None:
    # 恢复旧约束前必须清掉无法满足 NOT NULL / >= 1 的手动打卡记录。
    op.execute("DELETE FROM quiz_checkin WHERE first_attempt_id IS NULL")
    op.drop_constraint(
        'ck_quiz_checkin_questions_completed', 'quiz_checkin', type_='check'
    )
    op.create_check_constraint(
        'ck_quiz_checkin_questions_completed',
        'quiz_checkin',
        'questions_completed >= 1',
    )
    op.alter_column(
        'quiz_checkin',
        'first_attempt_id',
        existing_type=sa.BigInteger(),
        nullable=False,
    )
