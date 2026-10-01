"""NISP refund support

Revision ID: nisp002
Revises: quiz014_manual_checkin
Create Date: 2026-10-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = 'nisp002'
down_revision: Union[str, Sequence[str], None] = 'quiz014_manual_checkin'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Update registration status CHECK constraint to include refund states
    op.drop_constraint('ck_nisp_registration_status', 'nisp_registration', type_='check')
    op.create_check_constraint(
        'ck_nisp_registration_status',
        'nisp_registration',
        "status IN ('pending_payment', 'pending_review', "
        "'rejected_awaiting_resubmission', 'pending_refund_confirmation', "
        "'refund_processing', 'approved', 'refunded_closed', 'cancelled')",
    )

    # Update unique index WHERE clause
    op.drop_index('uq_nisp_registration_active_batch_idcard', table_name='nisp_registration')
    op.create_index(
        'uq_nisp_registration_active_batch_idcard',
        'nisp_registration',
        ['batch_id', 'candidate_idcard'],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('pending_payment', 'pending_review', "
            "'rejected_awaiting_resubmission', 'pending_refund_confirmation', "
            "'refund_processing', 'approved')"
        ),
    )

    # Create refund request table
    op.create_table(
        'nisp_refund_request',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('registration_id', sa.BigInteger(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('request_kind', sa.String(24), nullable=False),
        sa.Column('reason_code', sa.String(64), nullable=False),
        sa.Column('reason_detail', sa.Text(), nullable=True),
        sa.Column('amount_cents', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False, server_default='requested'),
        sa.Column('requested_by_admin_id', sa.Integer(), nullable=True),
        sa.Column('requested_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('approved_by_admin_id', sa.Integer(), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('out_refund_no', sa.String(64), nullable=True),
        sa.Column('wechat_refund_id', sa.String(64), nullable=True),
        sa.Column('processing_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('succeeded_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['registration_id'], ['nisp_registration.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['order_id'], ['order.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['requested_by_admin_id'], ['admin_user.id']),
        sa.ForeignKeyConstraint(['approved_by_admin_id'], ['admin_user.id']),
        sa.CheckConstraint(
            "request_kind IN ('review_failed', 'batch_cancelled', 'exception_close')",
            name='ck_nisp_refund_kind',
        ),
        sa.CheckConstraint(
            "status IN ('requested', 'approved', 'processing', 'succeeded', 'failed')",
            name='ck_nisp_refund_status',
        ),
        sa.CheckConstraint('amount_cents >= 0', name='ck_nisp_refund_amount_nonnegative'),
        sa.UniqueConstraint('out_refund_no'),
        sa.UniqueConstraint('wechat_refund_id'),
    )
    op.create_index('ix_nisp_refund_status', 'nisp_refund_request', ['status', 'id'])
    op.create_index(
        'uq_nisp_refund_active_order',
        'nisp_refund_request',
        ['order_id'],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('requested', 'approved', 'processing', 'failed')"
        ),
    )


def downgrade() -> None:
    op.drop_index('uq_nisp_refund_active_order', table_name='nisp_refund_request')
    op.drop_index('ix_nisp_refund_status', table_name='nisp_refund_request')
    op.drop_table('nisp_refund_request')
    op.drop_index('uq_nisp_registration_active_batch_idcard', table_name='nisp_registration')
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
    op.drop_constraint('ck_nisp_registration_status', 'nisp_registration', type_='check')
    op.create_check_constraint(
        'ck_nisp_registration_status',
        'nisp_registration',
        "status IN ('pending_payment', 'pending_review', "
        "'rejected_awaiting_resubmission', 'approved', 'cancelled')",
    )
