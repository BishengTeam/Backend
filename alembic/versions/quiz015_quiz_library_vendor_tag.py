"""quiz library vendor tag for practice assistant vendor filter

Revision ID: quiz015_vendor_tag
Revises: quiz014_manual_checkin

存量题库按名称关键字一次性回填；未命中的保持 none（小程序「其他」页签）。
上线前请先在目标库执行同名 UPDATE 语句 dry-run 核对回填映射。
"""
from alembic import op
from collections.abc import Sequence

import sqlalchemy as sa


revision: str = 'quiz015_vendor_tag'
down_revision: str | Sequence[str] = 'quiz014_manual_checkin'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_BACKFILL_SQL = (
    # 顺序即优先级；upper() 兼容 h3c/H3C 等大小写写法。
    "UPDATE quiz_library SET vendor_tag = 'h3c' "
    "WHERE vendor_tag = 'none' AND upper(name) LIKE '%H3C%'",
    "UPDATE quiz_library SET vendor_tag = 'nisp' "
    "WHERE vendor_tag = 'none' AND upper(name) LIKE '%NISP%'",
    "UPDATE quiz_library SET vendor_tag = 'sangfor' "
    "WHERE vendor_tag = 'none' AND name LIKE '%深信服%'",
)


def upgrade() -> None:
    op.add_column(
        'quiz_library',
        sa.Column(
            'vendor_tag',
            sa.String(length=16),
            nullable=False,
            server_default='none',
        ),
    )
    op.create_check_constraint(
        'ck_quiz_library_vendor_tag',
        'quiz_library',
        "vendor_tag IN ('h3c', 'nisp', 'sangfor', 'none')",
    )
    for statement in _BACKFILL_SQL:
        op.execute(statement)


def downgrade() -> None:
    op.drop_constraint(
        'ck_quiz_library_vendor_tag', 'quiz_library', type_='check'
    )
    op.drop_column('quiz_library', 'vendor_tag')
