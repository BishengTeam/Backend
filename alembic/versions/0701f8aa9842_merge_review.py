"""merge_review

Revision ID: 0701f8aa9842
Revises: be94a70b0170, r001_create_review
Create Date: 2026-06-19 23:39:38.244756

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0701f8aa9842'
down_revision: Union[str, Sequence[str], None] = ('be94a70b0170', 'r001_create_review')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
