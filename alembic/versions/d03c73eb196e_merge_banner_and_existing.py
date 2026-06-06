"""merge_banner_and_existing

Revision ID: d03c73eb196e
Revises: b0c1d2e3f4a5, f8e586b20e8c
Create Date: 2026-06-06 16:50:01.998888

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd03c73eb196e'
down_revision: Union[str, Sequence[str], None] = ('b0c1d2e3f4a5', 'f8e586b20e8c')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
