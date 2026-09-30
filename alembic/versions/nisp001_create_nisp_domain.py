"""create NISP certification domain

Revision ID: nisp001
Revises: ord001
Create Date: 2026-10-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = 'nisp001'
down_revision: Union[str, Sequence[str], None] = 'ord001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'nisp_exam_batch',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('plan_id', sa.Integer(), nullable=False),
        sa.Column('level', sa.String(4), nullable=False, server_default='1'),
        sa.Column('training_org', sa.String(128), nullable=True),
        sa.Column('training_teacher', sa.String(64), nullable=True),
        sa.Column('training_address', sa.String(256), nullable=True),
        sa.Column('training_start', sa.DateTime(timezone=True), nullable=True),
        sa.Column('training_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('level1_price_cents', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('level2_price_cents', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('payment_timeout_minutes', sa.Integer(), nullable=False, server_default='30'),
        sa.Column('resubmission_window_hours', sa.Integer(), nullable=False, server_default='72'),
        sa.Column('max_resubmissions', sa.Integer(), nullable=False, server_default='2'),
        sa.Column('max_material_bytes', sa.Integer(), nullable=False, server_default=str(10 * 1024 * 1024)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['plan_id'], ['plan.id'], ondelete='RESTRICT'),
        sa.UniqueConstraint('plan_id', name='uq_nisp_batch_plan'),
        sa.CheckConstraint("level IN ('1', '2')", name='ck_nisp_batch_level'),
        sa.CheckConstraint("level1_price_cents >= 0 AND level2_price_cents >= 0", name='ck_nisp_batch_prices_nonnegative'),
        sa.CheckConstraint("payment_timeout_minutes BETWEEN 1 AND 1440", name='ck_nisp_payment_timeout'),
        sa.CheckConstraint("resubmission_window_hours BETWEEN 1 AND 720", name='ck_nisp_resubmission_window'),
        sa.CheckConstraint("max_resubmissions BETWEEN 0 AND 10", name='ck_nisp_max_resubmissions'),
        sa.CheckConstraint("max_material_bytes BETWEEN 1 AND 20971520", name='ck_nisp_max_material_size'),
    )

    op.create_table(
        'nisp_registration',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('batch_id', sa.Integer(), nullable=False),
        sa.Column('plan_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('registration_no', sa.String(40), nullable=False),
        sa.Column('level', sa.String(4), nullable=False),
        sa.Column('status', sa.String(32), nullable=False),
        sa.Column('candidate_snapshot', JSONB(), nullable=False),
        sa.Column('candidate_idcard', sa.String(20), nullable=False),
        sa.Column('material_keys', JSONB(), nullable=True),
        sa.Column('resubmission_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('rejection_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('resubmission_due_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('close_reason', sa.String(128), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['batch_id'], ['nisp_exam_batch.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['plan_id'], ['plan.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['order_id'], ['order.id'], ondelete='RESTRICT'),
        sa.UniqueConstraint('registration_no', name='uq_nisp_registration_no'),
        sa.UniqueConstraint('order_id', name='uq_nisp_registration_order'),
        sa.CheckConstraint("level IN ('1', '2')", name='ck_nisp_registration_level'),
        sa.CheckConstraint(
            "status IN ('pending_payment', 'pending_review', 'rejected_awaiting_resubmission', 'approved', 'cancelled')",
            name='ck_nisp_registration_status',
        ),
        sa.CheckConstraint('resubmission_count >= 0', name='ck_nisp_resub_count'),
        sa.CheckConstraint('rejection_count >= 0', name='ck_nisp_rejection_count'),
    )
    op.create_index('ix_nisp_registration_user', 'nisp_registration', ['user_id'])
    op.create_index('ix_nisp_registration_level', 'nisp_registration', ['level'])
    op.create_index('ix_nisp_registration_status', 'nisp_registration', ['status'])
    op.create_index('ix_nisp_registration_batch_status', 'nisp_registration', ['batch_id', 'status'])
    op.create_index('ix_nisp_registration_resubmission_due', 'nisp_registration', ['status', 'resubmission_due_at'])
    op.create_index(
        'uq_nisp_registration_active_batch_idcard',
        'nisp_registration',
        ['batch_id', 'candidate_idcard'],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('pending_payment', 'pending_review', "
            "'rejected_awaiting_resubmission', 'approved')"
        ),
    )

    op.create_table(
        'nisp_review',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('registration_id', sa.BigInteger(), nullable=False),
        sa.Column('decision', sa.String(16), nullable=False),
        sa.Column('reason_code', sa.String(64), nullable=True),
        sa.Column('reason_detail', sa.Text(), nullable=True),
        sa.Column('rejected_material_types', JSONB(), nullable=True),
        sa.Column('material_version_ids', JSONB(), nullable=True),
        sa.Column('reviewer_admin_id', sa.Integer(), nullable=False),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['registration_id'], ['nisp_registration.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['reviewer_admin_id'], ['admin_user.id'], ondelete='RESTRICT'),
        sa.CheckConstraint("decision IN ('approved', 'rejected')", name='ck_nisp_review_decision'),
    )
    op.create_index('ix_nisp_review_registration', 'nisp_review', ['registration_id'])


def downgrade() -> None:
    op.drop_index('ix_nisp_review_registration', table_name='nisp_review')
    op.drop_table('nisp_review')
    op.drop_index('uq_nisp_registration_active_batch_idcard', table_name='nisp_registration')
    op.drop_index('ix_nisp_registration_resubmission_due', table_name='nisp_registration')
    op.drop_index('ix_nisp_registration_batch_status', table_name='nisp_registration')
    op.drop_index('ix_nisp_registration_status', table_name='nisp_registration')
    op.drop_index('ix_nisp_registration_level', table_name='nisp_registration')
    op.drop_index('ix_nisp_registration_user', table_name='nisp_registration')
    op.drop_table('nisp_registration')
    op.drop_table('nisp_exam_batch')
