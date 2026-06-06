"""merge_banner_user_identity_heads

Revision ID: m001_merge_heads
Revises: b0c1d2e3f4a5, f8e586b20e8c
Create Date: 2026-06-06

合并 add_banner_fields_to_zone 和 add_profile_fields_to_user_identity 两个分支
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'm001_merge_heads'
down_revision: Union[str, Sequence[str], None] = ('b0c1d2e3f4a5', 'f8e586b20e8c')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
