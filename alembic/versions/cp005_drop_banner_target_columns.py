"""drop banner target_type/target_id columns

Revision ID: cp005_drop_banner_targets
Revises: cp004_cert_product_catalog
"""
from alembic import op
from collections.abc import Sequence

import sqlalchemy as sa


revision: str = 'cp005_drop_banner_targets'
down_revision: str | Sequence[str] = 'cp004_cert_product_catalog'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column('banner', 'target_type')
    op.drop_column('banner', 'target_id')


def downgrade() -> None:
    op.add_column('banner', sa.Column('target_type', sa.String(length=32), nullable=True))
    op.add_column('banner', sa.Column('target_id', sa.Integer(), nullable=True))
