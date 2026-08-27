"""replace h3c_admin role with cert_admin

Revision ID: cp003_replace_h3c_admin_role
Revises: cp002_seed_cert_product
"""
from alembic import op
from collections.abc import Sequence

import sqlalchemy as sa


revision: str = 'cp003_replace_h3c_admin_role'
down_revision: str | Sequence[str] = 'cp002_seed_cert_product'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_ROLE_CHECK = "role IN ('super_admin', 'quiz_admin', 'cert_admin', 'course_admin')"
OLD_ROLE_CHECK = "role IN ('super_admin', 'quiz_admin', 'h3c_admin', 'course_admin')"


def upgrade() -> None:
    op.execute(sa.text(
        "UPDATE admin_user SET role = 'cert_admin' WHERE role = 'h3c_admin'"
    ))
    op.drop_constraint('ck_admin_user_role', 'admin_user', type_='check')
    op.create_check_constraint(
        'ck_admin_user_role',
        'admin_user',
        NEW_ROLE_CHECK,
    )


def downgrade() -> None:
    op.execute(sa.text(
        "UPDATE admin_user SET role = 'h3c_admin' WHERE role = 'cert_admin'"
    ))
    op.drop_constraint('ck_admin_user_role', 'admin_user', type_='check')
    op.create_check_constraint(
        'ck_admin_user_role',
        'admin_user',
        OLD_ROLE_CHECK,
    )
