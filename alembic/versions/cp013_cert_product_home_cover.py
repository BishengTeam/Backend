"""cert product home cover url

Revision ID: cp013_cert_home_cover
Revises: rsh002_review_export_binding
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'cp013_cert_home_cover'
down_revision: str | Sequence[str] = 'rsh002_review_export_binding'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'cert_product',
        sa.Column('home_cover_url', sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('cert_product', 'home_cover_url')
