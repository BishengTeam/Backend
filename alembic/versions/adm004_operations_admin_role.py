"""Add operations_admin role.

运营管理员：积分商城 + 首页运营 + 赛事与活动 + 培训与招聘 + 内容与文档，
并附带课程只读、题库只读（积分商城「关联内容」下拉需要）。

Revision ID: adm004
Revises: adm003
"""
from alembic import op
import sqlalchemy as sa


revision = "adm004"
down_revision = "adm003"
branch_labels = None
depends_on = None

ROLES = (
    "super_admin",
    "quiz_admin",
    "cert_admin",
    "course_admin",
    "teacher",
    "operations_admin",
)
PREVIOUS_ROLES = ROLES[:-1]


def _role_check(roles: tuple[str, ...]) -> str:
    return "role IN (" + ", ".join(f"'{role}'" for role in roles) + ")"


def upgrade() -> None:
    if op.get_context().as_sql:
        return
    op.drop_constraint("ck_admin_user_role", "admin_user", type_="check")
    op.create_check_constraint(
        "ck_admin_user_role", "admin_user", _role_check(ROLES)
    )


def downgrade() -> None:
    if op.get_context().as_sql:
        return
    conn = op.get_bind()
    remaining = conn.execute(
        sa.text(
            "SELECT count(*) FROM admin_user WHERE role = 'operations_admin'"
        )
    ).scalar()
    if remaining:
        raise RuntimeError(
            "存在 operations_admin 账号，请先将其改为其他角色再回滚本次迁移"
        )
    op.drop_constraint("ck_admin_user_role", "admin_user", type_="check")
    op.create_check_constraint(
        "ck_admin_user_role", "admin_user", _role_check(PREVIOUS_ROLES)
    )
