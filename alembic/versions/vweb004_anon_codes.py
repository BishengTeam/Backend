"""course_code: allow anonymous issued codes

Revision ID: vweb004_anon_codes
Revises: vweb002_user_access

匿名兑换码：生成的码在兑换前没有归属人，兑换时才绑定兑换人。
约束放宽为 issued/revoked/refunded 允许 owner 为空；redeemed 仍必须有 owner。
"""
from alembic import op

revision = "vweb004_anon_codes"
down_revision = "vweb003_user_note"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_course_code_owner", "course_code", type_="check")
    op.create_check_constraint(
        "ck_course_code_owner",
        "course_code",
        "status IN ('issued', 'revoked', 'refunded') OR owner_user_id IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint("ck_course_code_owner", "course_code", type_="check")
    op.create_check_constraint(
        "ck_course_code_owner",
        "course_code",
        "status IN ('revoked', 'refunded') OR owner_user_id IS NOT NULL",
    )
