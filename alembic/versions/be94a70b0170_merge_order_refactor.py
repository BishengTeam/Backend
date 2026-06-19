"""merge_order_refactor

Revision ID: be94a70b0170
Revises: 7e2140a74425, o001_order_refactor
Create Date: 2026-06-19 22:29:59.356832

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'be94a70b0170'
down_revision: Union[str, Sequence[str], None] = ('7e2140a74425', 'o001_order_refactor')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
