"""allow teacher admin role

Revision ID: cls002_teacher_role
Revises: cls001_classroom_module
"""
from alembic import op
from collections.abc import Sequence

import sqlalchemy as sa


revision: str = 'cls002_teacher_role'
down_revision: str | Sequence[str] = 'cls001_classroom_module'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_CHECK = "role IN ('super_admin', 'quiz_admin', 'cert_admin', 'course_admin', 'teacher')"
OLD_CHECK = "role IN ('super_admin', 'quiz_admin', 'cert_admin', 'course_admin')"


def upgrade() -> None:
    op.drop_constraint('ck_admin_user_role', 'admin_user', type_='check')
    op.create_check_constraint('ck_admin_user_role', 'admin_user', NEW_CHECK)


def downgrade() -> None:
    op.drop_constraint('ck_admin_user_role', 'admin_user', type_='check')
    op.create_check_constraint('ck_admin_user_role', 'admin_user', OLD_CHECK)
