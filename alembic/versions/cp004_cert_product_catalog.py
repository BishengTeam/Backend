"""cert product catalog + seed h3c products from price sheet

Revision ID: cp004_cert_product_catalog
Revises: cp003_replace_h3c_admin_role
"""
from alembic import op
from collections.abc import Sequence

import sqlalchemy as sa


revision: str = 'cp004_cert_product_catalog'
down_revision: str | Sequence[str] = 'cp003_replace_h3c_admin_role'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 来源: docs/h3c/考试认证价格.xlsx / 新华三考试详情
# (code, name, duration, questions, total, pass, validity_years, retakes,
#  normal_cents, student_cents|None)
SEED = [
    ("GB0-170", "H3C认证物联网技术工程师 H3CNE-IoT", 60, 50, 1000, 600, 3, 0, 120000, None),
    ("GB0-192", "H3CNE-RS+ H3C认证路由交换网络工程师", 60, 50, 1000, 600, 3, 0, 120000, None),
    ("GB0-713", "H3C认证云计算工程师 H3C Certified Network Engineer for Cloud", 60, 50, 1000, 600, 3, 0, 120000, 50000),
    ("GB0-510", "H3C认证网络安全工程师（H3CNE-Security）", 60, 50, 1000, 600, 3, 0, 120000, 50000),
    ("GB0-410", "H3CNE-5G H3C认证5G网络工程师(H3C Certified Network Engineer for 5G)", 60, 50, 1000, 600, 3, 0, 120000, None),
    ("GB0-404", "H3CNE-BigData(H3C Certified Network Engineer for BigData)", 60, 50, 1000, 600, 3, 0, 120000, None),
    ("GB0-420", "H3C认证工业互联网技术工程师(H3CNE-II)", 60, 50, 1000, 600, 3, 0, 120000, None),
]


def _slug(code: str) -> str:
    return code.lower().replace('-', '_')


def upgrade() -> None:
    op.create_table(
        'cert_product_catalog',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('type', sa.String(length=32), nullable=False),
        sa.Column('code', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=256), nullable=False),
        sa.Column('duration_minutes', sa.Integer(), nullable=True),
        sa.Column('question_count', sa.Integer(), nullable=True),
        sa.Column('total_score', sa.Integer(), nullable=True),
        sa.Column('pass_score', sa.Integer(), nullable=True),
        sa.Column('cert_validity_years', sa.Integer(), nullable=True),
        sa.Column('retake_count', sa.Integer(), nullable=True),
        sa.Column('prerequisite', sa.Text(), nullable=True),
        sa.Column('remark', sa.String(length=256), nullable=True),
        sa.Column('source', sa.String(length=128), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('type', 'code', name='uq_cert_catalog_type_code'),
    )
    op.create_index('ix_cert_product_catalog_type', 'cert_product_catalog', ['type'])

    op.add_column(
        'cert_product',
        sa.Column('catalog_id', sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        'fk_cert_product_catalog',
        'cert_product',
        'cert_product_catalog',
        ['catalog_id'],
        ['id'],
        ondelete='SET NULL',
    )

    # 录入目录 + 产品 + 默认价格（幂等：已存在则跳过）
    for code, name, dur, qn, total, passing, years, retakes, normal_cents, student_cents in SEED:
        op.execute(sa.text(f"""
            INSERT INTO cert_product_catalog
                (type, code, name, duration_minutes, question_count, total_score,
                 pass_score, cert_validity_years, retake_count, prerequisite, remark, source)
            VALUES ('h3c', '{code}', '{_esc(name)}', {dur}, {qn}, {total},
                    {passing}, {years}, {retakes}, '无', '在线考试', '考试认证价格.xlsx')
            ON CONFLICT (type, code) DO NOTHING
        """))
        op.execute(sa.text(f"""
            INSERT INTO cert_product (type, catalog_id, code, name, chinese_name, description, is_active, sort_order)
            VALUES ('h3c',
                    (SELECT id FROM cert_product_catalog WHERE type='h3c' AND code='{code}'),
                    '{code}', '{_slug(code)}', '{_esc(name)}', NULL, true, 0)
            ON CONFLICT (code) DO NOTHING
        """))
        op.execute(sa.text(f"""
            INSERT INTO price_config (product_type, user_type, price, is_active)
            VALUES ('{code}', 'normal', {normal_cents}, true)
            ON CONFLICT DO NOTHING
        """))
        if student_cents is not None:
            op.execute(sa.text(f"""
                INSERT INTO price_config (product_type, user_type, price, is_active)
                VALUES ('{code}', 'student', {student_cents}, true)
                ON CONFLICT DO NOTHING
            """))


def downgrade() -> None:
    for code, *_rest in SEED:
        op.execute(sa.text(f"DELETE FROM price_config WHERE product_type = '{code}'"))
        op.execute(sa.text(f"DELETE FROM cert_product WHERE code = '{code}'"))
    op.drop_constraint('fk_cert_product_catalog', 'cert_product', type_='foreignkey')
    op.drop_column('cert_product', 'catalog_id')
    op.drop_index('ix_cert_product_catalog_type', table_name='cert_product_catalog')
    op.drop_table('cert_product_catalog')


def _esc(s: str) -> str:
    return s.replace("'", "''")
