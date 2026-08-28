"""competition entity + tracks + registration fields

Revision ID: cp008_competition_mgmt
Revises: cp007_activity_marketing
"""
from alembic import op
from collections.abc import Sequence

import sqlalchemy as sa


revision: str = 'cp008_competition_mgmt'
down_revision: str | Sequence[str] = 'cp007_activity_marketing'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'competition',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('cover_url', sa.String(length=512), nullable=True),
        sa.Column('start_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('end_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('registration_deadline', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'competition_track',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('competition_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('max_participants', sa.Integer(), server_default='0', nullable=False),
        sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['competition_id'], ['competition.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_competition_track_competition_id', 'competition_track', ['competition_id'])
    op.add_column('competition_reg', sa.Column('track_id', sa.Integer(), nullable=True))
    op.add_column('competition_reg', sa.Column('real_name', sa.String(length=64), nullable=True))
    op.add_column('competition_reg', sa.Column('phone', sa.String(length=20), nullable=True))
    op.create_foreign_key('fk_comp_reg_track', 'competition_reg', 'competition_track', ['track_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint('fk_comp_reg_track', 'competition_reg', type_='foreignkey')
    op.drop_column('competition_reg', 'phone')
    op.drop_column('competition_reg', 'real_name')
    op.drop_column('competition_reg', 'track_id')
    op.drop_index('ix_competition_track_competition_id', table_name='competition_track')
    op.drop_table('competition_track')
    op.drop_table('competition')
