"""points user FK cascade

Revision ID: pts001
Revises: pm001
Create Date: 2026-09-23
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'pts001'
down_revision: Union[str, Sequence[str], None] = 'pm001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint('user_points_user_id_fkey', 'user_points', type_='foreignkey')
    op.create_foreign_key(
        'user_points_user_id_fkey', 'user_points', 'user',
        ['user_id'], ['id'], ondelete='CASCADE',
    )
    op.drop_constraint('points_history_user_id_fkey', 'points_history', type_='foreignkey')
    op.create_foreign_key(
        'points_history_user_id_fkey', 'points_history', 'user',
        ['user_id'], ['id'], ondelete='CASCADE',
    )


def downgrade() -> None:
    op.drop_constraint('points_history_user_id_fkey', 'points_history', type_='foreignkey')
    op.create_foreign_key(
        'points_history_user_id_fkey', 'points_history', 'user',
        ['user_id'], ['id'],
    )
    op.drop_constraint('user_points_user_id_fkey', 'user_points', type_='foreignkey')
    op.create_foreign_key(
        'user_points_user_id_fkey', 'user_points', 'user',
        ['user_id'], ['id'],
    )
