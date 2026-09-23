"""order coupon fields

Revision ID: ord001
Revises: pts001
Create Date: 2026-09-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'ord001'
down_revision: Union[str, Sequence[str], None] = 'pts001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('order', sa.Column('original_price', sa.Integer(), nullable=True, comment='折前原价，无折扣时为空'))
    op.add_column('order', sa.Column('discount_amount', sa.Integer(), nullable=True, comment='优惠减免金额'))
    op.add_column('order', sa.Column('coupon_code', sa.String(64), nullable=True, comment='使用的积分商城券码'))
    op.create_index('ix_order_coupon_code', 'order', ['coupon_code'])


def downgrade() -> None:
    op.drop_index('ix_order_coupon_code', table_name='order')
    op.drop_column('order', 'coupon_code')
    op.drop_column('order', 'discount_amount')
    op.drop_column('order', 'original_price')
