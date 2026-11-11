"""competition.quiz_library_id（赛事绑定练习题库，详情页透出练习入口）

Revision ID: cp021_competition_quiz_library
Revises: quiz023
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'cp021_competition_quiz_library'
down_revision: str | Sequence[str] = 'quiz023'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'competition',
        sa.Column('quiz_library_id', sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        'fk_competition_quiz_library_id',
        'competition',
        'quiz_library',
        ['quiz_library_id'],
        ['id'],
        ondelete='RESTRICT',
    )
    op.create_index(
        'ix_competition_quiz_library_id',
        'competition',
        ['quiz_library_id'],
    )


def downgrade() -> None:
    op.drop_index('ix_competition_quiz_library_id', table_name='competition')
    op.drop_constraint(
        'fk_competition_quiz_library_id', 'competition', type_='foreignkey'
    )
    op.drop_column('competition', 'quiz_library_id')
