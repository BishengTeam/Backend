"""reactivate legacy H3C certification rows

Revision ID: cp009_reactivate_h3c
Revises: cp008_competition_mgmt
"""
from alembic import op
from collections.abc import Sequence

import sqlalchemy as sa


revision: str = 'cp009_reactivate_h3c'
down_revision: str | Sequence[str] = 'merge_heads_002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # H3C 批次创建/下单链路仍依赖旧 certification 表激活状态；
    # 认证管理重构后旧表被误下架导致小程序批次列表为空、下单被拒。
    op.execute(sa.text("UPDATE certification SET is_active = true WHERE vendor = 'H3C'"))


def downgrade() -> None:
    op.execute(sa.text("UPDATE certification SET is_active = false WHERE vendor = 'H3C' AND code != 'RS-ZY'"))
