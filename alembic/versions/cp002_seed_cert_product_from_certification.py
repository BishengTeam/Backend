"""seed cert_product from existing certification table

Revision ID: cp002_seed_cert_product
Revises: cp001_create_cert_product, quiz009
"""
from alembic import op
from collections.abc import Sequence

import sqlalchemy as sa


# vendor(旧) → type(新) 大小写映射
VENDOR_MAP = {
    'H3C': 'h3c',
    'h3c': 'h3c',
    '人社': 'renshe',
    'renshe': 'renshe',
    '深信服': 'sangfor',
    'NISP': 'nisp',
}

revision: str = 'cp002_seed_cert_product'
down_revision: str | Sequence[str] = ('cp001_create_cert_product', 'quiz009')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 离线 SQL 模式不支持数据迁移，安全跳过
    if op.get_context().is_offline_mode():
        return
    conn = op.get_bind()
    # 表可能不存在（CI 干净环境），安全跳过
    has_table = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='certification')"
    )).scalar()
    if not has_table:
        return
    old_rows = conn.execute(sa.text('SELECT code, vendor, name, chinese_name, is_active FROM certification')).fetchall()
    for row in old_rows:
        code, vendor, name, chinese_name, is_active = row
        new_type = VENDOR_MAP.get(vendor)
        if not new_type:
            continue
        exists = conn.execute(
            sa.text('SELECT 1 FROM cert_product WHERE code = :code'),
            {'code': code},
        ).scalar()
        if exists:
            continue
        conn.execute(
            sa.text('''
                INSERT INTO cert_product (type, code, name, chinese_name, is_active, sort_order)
                VALUES (:type, :code, :name, :chinese_name, :is_active, 0)
            '''),
            {
                'type': new_type,
                'code': code,
                'name': name,
                'chinese_name': chinese_name,
                'is_active': is_active,
            },
        )


def downgrade() -> None:
    op.execute(sa.text('DELETE FROM cert_product'))
