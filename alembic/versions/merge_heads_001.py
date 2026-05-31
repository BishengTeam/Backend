"""merge heads into single linear chain (no-op)

Revision ID: merge_heads_001
Revises: b1c2d3e4f5a7
Create Date: 2026-05-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "merge_heads_001"
down_revision: Union[str, Sequence[str], None] = "b1c2d3e4f5a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
