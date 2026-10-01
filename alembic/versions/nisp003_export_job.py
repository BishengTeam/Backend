"""NISP async export job

Revision ID: nisp003
Revises: nisp002
Create Date: 2026-10-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = 'nisp003'
down_revision: Union[str, Sequence[str], None] = 'nisp002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'nisp_export_job',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('batch_id', sa.Integer(), nullable=False),
        sa.Column('level', sa.String(4), nullable=False),
        sa.Column('requested_by_admin_id', sa.Integer(), nullable=False),
        sa.Column('include_statuses', JSONB(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False, server_default='queued'),
        sa.Column('registration_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('storage_key', sa.String(512), nullable=True),
        sa.Column('artifact_sha256', sa.String(64), nullable=True),
        sa.Column('artifact_bytes', sa.Integer(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['batch_id'], ['nisp_exam_batch.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['requested_by_admin_id'], ['admin_user.id'], ondelete='RESTRICT'),
        sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed')", name='ck_nisp_export_job_status'),
        sa.CheckConstraint("level IN ('1', '2')", name='ck_nisp_export_level'),
        sa.CheckConstraint('registration_count >= 0', name='ck_nisp_export_count_nonnegative'),
    )
    op.create_index('ix_nisp_export_status', 'nisp_export_job', ['status', 'id'])
    op.create_index('ix_nisp_export_expires', 'nisp_export_job', ['status', 'expires_at'])


def downgrade() -> None:
    op.drop_index('ix_nisp_export_expires', table_name='nisp_export_job')
    op.drop_index('ix_nisp_export_status', table_name='nisp_export_job')
    op.drop_table('nisp_export_job')
