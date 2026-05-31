"""add role CHECK constraint to admin_user

Revision ID: e8f9a0b1c2d3
Revises: c8d9e0f1a2b3
Create Date: 2026-05-19 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'e8f9a0b1c2d3'
down_revision: Union[str, Sequence[str], None] = 'c8d9e0f1a2b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ROLES = ("super_admin", "content_editor", "customer_service", "finance", "auditor")


def upgrade() -> None:
    op.create_check_constraint(
        "ck_admin_user_role",
        "admin_user",
        f"role = ANY (ARRAY[{', '.join(repr(r) for r in ROLES)}])",
    )


def downgrade() -> None:
    op.drop_constraint("ck_admin_user_role", "admin_user")
