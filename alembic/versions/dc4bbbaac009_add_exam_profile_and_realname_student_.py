"""add_exam_profile_and_realname_student_fields

Revision ID: dc4bbbaac009
Revises: 0701f8aa9842
Create Date: 2026-06-26 04:12:26.327011

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'dc4bbbaac009'
down_revision: Union[str, Sequence[str], None] = '0701f8aa9842'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # user_profile 地址字段
    op.add_column('user_profile', sa.Column('province', sa.String(length=32), nullable=True))
    op.add_column('user_profile', sa.Column('city', sa.String(length=32), nullable=True))
    op.add_column('user_profile', sa.Column('address', sa.String(length=256), nullable=True))
    # order extra_data 注释
    op.alter_column('order', 'extra_data',
               existing_type=postgresql.JSON(astext_type=sa.Text()),
               comment='按 product_type 存的差异化报名数据',
               existing_comment='按 cert_type 存的差异化报名数据',
               existing_nullable=True)
    # price_config 索引
    op.drop_index(op.f('uq_price_config_active_product_user'), table_name='price_config', postgresql_where='(is_active IS TRUE)')
    op.create_index('uq_price_config_active_cert_user', 'price_config', ['product_type', 'user_type'], unique=True, postgresql_where=sa.text('is_active IS true'))
    # user_realname
    op.add_column('user_realname', sa.Column('last_name_zh', sa.String(length=32), nullable=True))
    op.add_column('user_realname', sa.Column('first_name_zh', sa.String(length=32), nullable=True))
    op.add_column('user_realname', sa.Column('last_name_en', sa.String(length=64), nullable=True))
    op.add_column('user_realname', sa.Column('first_name_en', sa.String(length=64), nullable=True))
    op.add_column('user_realname', sa.Column('avatar_oss', sa.String(length=512), nullable=True))
    op.add_column('user_realname', sa.Column('birth_date', sa.String(length=10), nullable=True))
    op.add_column('user_realname', sa.Column('zip_code', sa.String(length=6), nullable=True))
    op.add_column('user_realname', sa.Column('political_status', sa.String(length=16), nullable=True))
    op.add_column('user_realname', sa.Column('ethnicity', sa.String(length=16), nullable=True))
    # user_student
    op.add_column('user_student', sa.Column('enrollment_pdf_oss', sa.String(length=512), nullable=True))
    op.add_column('user_student', sa.Column('degree_cert_oss', sa.String(length=512), nullable=True))
    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('user_student', 'degree_cert_oss')
    op.drop_column('user_student', 'enrollment_pdf_oss')
    op.drop_column('user_realname', 'ethnicity')
    op.drop_column('user_realname', 'political_status')
    op.drop_column('user_realname', 'zip_code')
    op.drop_column('user_realname', 'birth_date')
    op.drop_column('user_realname', 'avatar_oss')
    op.drop_column('user_realname', 'first_name_en')
    op.drop_column('user_realname', 'last_name_en')
    op.drop_column('user_realname', 'first_name_zh')
    op.drop_column('user_realname', 'last_name_zh')
    op.drop_index('uq_price_config_active_cert_user', table_name='price_config', postgresql_where=sa.text('is_active IS true'))
    op.create_index(op.f('uq_price_config_active_product_user'), 'price_config', ['product_type', 'user_type'], unique=True, postgresql_where='(is_active IS TRUE)')
    op.alter_column('order', 'extra_data',
               existing_type=postgresql.JSON(astext_type=sa.Text()),
               comment='按 cert_type 存的差异化报名数据',
               existing_comment='按 product_type 存的差异化报名数据',
               existing_nullable=True)
    op.drop_column('user_profile', 'address')
    op.drop_column('user_profile', 'city')
    op.drop_column('user_profile', 'province')
    # ### end Alembic commands ###
