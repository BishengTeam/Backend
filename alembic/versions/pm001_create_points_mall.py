"""create points mall tables

Revision ID: pm001
Revises: agr004
Create Date: 2026-09-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'pm001'
down_revision: Union[str, Sequence[str], None] = 'agr004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'points_mall_item',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('description', sa.String(256), nullable=True),
        sa.Column('discount_type', sa.String(16), nullable=False),
        sa.Column('discount_value', sa.Integer(), nullable=False),
        sa.Column('scope_type', sa.String(16), nullable=False, server_default='global'),
        sa.Column('scope_value', sa.String(64), nullable=True),
        sa.Column('min_order_amount_cents', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('points_cost', sa.Integer(), nullable=False),
        sa.Column('total_stock', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_redeemed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('per_user_limit', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('validity_type', sa.String(16), nullable=False, server_default='days'),
        sa.Column('validity_days', sa.Integer(), nullable=True),
        sa.Column('valid_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint("discount_type IN ('fixed', 'percent')", name='ck_pm_item_discount_type'),
        sa.CheckConstraint('discount_value > 0', name='ck_pm_item_discount_positive'),
        sa.CheckConstraint("scope_type IN ('global', 'category', 'product')", name='ck_pm_item_scope_type'),
        sa.CheckConstraint("validity_type IN ('days', 'fixed_date')", name='ck_pm_item_validity_type'),
        sa.CheckConstraint('points_cost > 0', name='ck_pm_item_points_positive'),
        sa.CheckConstraint('total_stock >= 0 AND total_redeemed >= 0 AND per_user_limit >= 0', name='ck_pm_item_limits_nonnegative'),
        sa.CheckConstraint('min_order_amount_cents >= 0', name='ck_pm_item_min_order_nonnegative'),
    )
    op.create_index('ix_pm_item_active_sort', 'points_mall_item', ['is_active', 'sort_order'])

    op.create_table(
        'points_mall_redemption',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('item_id', sa.BigInteger(), nullable=False),
        sa.Column('points_spent', sa.Integer(), nullable=False),
        sa.Column('coupon_code', sa.String(64), nullable=False),
        sa.Column('name_snapshot', sa.String(128), nullable=False),
        sa.Column('discount_type', sa.String(16), nullable=False),
        sa.Column('discount_value', sa.Integer(), nullable=False),
        sa.Column('scope_type', sa.String(16), nullable=False),
        sa.Column('scope_value', sa.String(64), nullable=True),
        sa.Column('min_order_amount_cents', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('status', sa.String(16), nullable=False, server_default='unused'),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('order_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['item_id'], ['points_mall_item.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['order_id'], ['order.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('coupon_code'),
        sa.CheckConstraint('points_spent > 0', name='ck_pm_redemption_points_positive'),
        sa.CheckConstraint("discount_type IN ('fixed', 'percent')", name='ck_pm_redemption_discount_type'),
    )
    op.create_index('ix_pm_redemption_user', 'points_mall_redemption', ['user_id', 'status'])
    op.create_index('ix_pm_redemption_item', 'points_mall_redemption', ['item_id'])
    op.create_index(
        'uq_pm_redemption_order',
        'points_mall_redemption',
        ['order_id'],
        unique=True,
        postgresql_where=sa.text('order_id IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('uq_pm_redemption_order', table_name='points_mall_redemption')
    op.drop_index('ix_pm_redemption_item', table_name='points_mall_redemption')
    op.drop_index('ix_pm_redemption_user', table_name='points_mall_redemption')
    op.drop_table('points_mall_redemption')
    op.drop_index('ix_pm_item_active_sort', table_name='points_mall_item')
    op.drop_table('points_mall_item')
