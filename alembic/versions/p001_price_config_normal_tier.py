"""rename enterprise price tier to normal

Revision ID: p001_price_config_normal_tier
Revises: dc4bbbaac009
Create Date: 2026-07-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "p001_price_config_normal_tier"
down_revision: Union[str, Sequence[str], None] = "dc4bbbaac009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE price_config
        SET user_type = 'normal'
        WHERE user_type = 'enterprise'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE price_config
        SET user_type = 'enterprise'
        WHERE user_type = 'normal'
        """
    )

