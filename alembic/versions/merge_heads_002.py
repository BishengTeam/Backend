"""merge cp008 competition and quiz010 wrong-book heads (no-op)

Revision ID: merge_heads_002
Revises: cp008_competition_mgmt, quiz010
Create Date: 2026-08-30
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "merge_heads_002"
down_revision: Union[str, Sequence[str], None] = (
    "cp008_competition_mgmt",
    "quiz010",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
