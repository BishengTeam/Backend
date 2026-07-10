"""merge_plan

Revision ID: 8e233e41d4bf
Revises: p001_price_config_normal_tier, p002_plan_table
Create Date: 2026-07-07 18:14:36.975951

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8e233e41d4bf'
down_revision: Union[str, Sequence[str], None] = ('p001_price_config_normal_tier', 'p002_plan_table')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
