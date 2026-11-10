"""gzh_articles admin manual maintenance: source column + recycle-bin soft delete

公众号资讯 Admin 手动维护（方案 §5.4 退路）：
- source 区分 sync（同步 worker 维护）/ manual（Admin 手动维护，worker 不覆盖不清理）
- 补回收站软删两列（与 rb001 同款），删除进统一回收站，30 自然日可恢复

Revision ID: cp020_gzh_article_admin
Revises: yc001_yingcai_category
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'cp020_gzh_article_admin'
down_revision: str | Sequence[str] = 'yc001_yingcai_category'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'gzh_articles',
        sa.Column('source', sa.String(length=16), nullable=False, server_default='sync'),
    )
    op.add_column('gzh_articles', sa.Column('deleted_at', sa.DateTime(timezone=True)))
    op.add_column('gzh_articles', sa.Column('restore_until', sa.DateTime(timezone=True)))
    # 部分索引：列表查询 WHERE deleted_at IS NULL 走索引扫描（同 rb001）
    op.create_index(
        'ix_gzh_articles_deleted_at', 'gzh_articles', ['deleted_at'],
        postgresql_where=sa.text('deleted_at IS NULL'),
        sqlite_where=sa.text('deleted_at IS NULL'),
    )


def downgrade() -> None:
    op.drop_index('ix_gzh_articles_deleted_at', table_name='gzh_articles')
    op.drop_column('gzh_articles', 'restore_until')
    op.drop_column('gzh_articles', 'deleted_at')
    op.drop_column('gzh_articles', 'source')
