"""create cert_product table

Revision ID: cp001_create_cert_product
Revises: h3c001
"""
from alembic import op
import sqlalchemy as sa

revision = "cp001_create_cert_product"
down_revision = "h3c001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'cert_product',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('type', sa.String(32), nullable=False, index=True),
        sa.Column('code', sa.String(32), nullable=False, unique=True),
        sa.Column('name', sa.String(64), nullable=False),
        sa.Column('chinese_name', sa.String(128), nullable=False),
        sa.Column('description', sa.String(512), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=True),
        sa.Column('sort_order', sa.Integer(), server_default='0', nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('cert_product')
