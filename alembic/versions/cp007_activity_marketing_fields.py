"""activity marketing fields (related resources, live/group entry, deadline)

Revision ID: cp007_activity_marketing
Revises: cp006_drop_zone_dead_columns
"""
from alembic import op
from collections.abc import Sequence

import sqlalchemy as sa


revision: str = 'cp007_activity_marketing'
down_revision: str | Sequence[str] = 'cp006_drop_zone_dead_columns'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('activity', sa.Column('related_cert_id', sa.Integer(), nullable=True))
    op.add_column('activity', sa.Column('related_course_id', sa.Integer(), nullable=True))
    op.add_column('activity', sa.Column('live_url', sa.String(length=512), nullable=True))
    op.add_column('activity', sa.Column('group_qrcode_url', sa.String(length=512), nullable=True))
    op.add_column('activity', sa.Column('registration_deadline', sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key('fk_activity_related_cert', 'activity', 'certification', ['related_cert_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('fk_activity_related_course', 'activity', 'course', ['related_course_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint('fk_activity_related_course', 'activity', type_='foreignkey')
    op.drop_constraint('fk_activity_related_cert', 'activity', type_='foreignkey')
    op.drop_column('activity', 'registration_deadline')
    op.drop_column('activity', 'group_qrcode_url')
    op.drop_column('activity', 'live_url')
    op.drop_column('activity', 'related_course_id')
    op.drop_column('activity', 'related_cert_id')
