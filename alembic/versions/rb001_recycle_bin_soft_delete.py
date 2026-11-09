"""Unified recycle bin: soft-delete columns for admin hard-delete tables,
user deactivation marker, and 30-day restore window extension.

全后台统一回收站：
- 管理端原物理删除的内容表补 deleted_at / restore_until（删除进回收站，30 自然日倒计时）
- user.deactivated_at 区分「禁用」与「已注销」（注销不进回收站且后台不可见）
- quiz 域存量未到期软删行从 7 天窗口顺延到 30 天
"""

from alembic import op
import sqlalchemy as sa

revision = "rb001_recycle_bin"
# 2026-11-09 rebase：远程 cbr001（认证批次重构）同挂 quiz020，本迁移改挂其后避免双 head
down_revision = "cbr001"
branch_labels = None
depends_on = None

# 原物理删除的内容表，统一补软删列进回收站。
# 不进清单的：plan/renshe 项目/quiz_category（草稿守卫删除 + 部分唯一索引，软删会阻塞同名重建，
# 保持原状）；prices/coupons/cert_products/certifications/points_mall（is_active 下架类，非删除）。
_SOFT_DELETE_TABLES = [
    "banner",
    "popup",
    "zone",
    "share_poster",
    "training",
    "activity",
    "job",
    "competition",
    "classroom_video",
    "classroom_question",
    "course",
    "course_chapter",
]

# quiz 域四类对象已有软删列，仅把存量未到期行的 7 天窗口顺延到 30 天。
_QUIZ_SOFT_DELETE_TABLES = [
    "quiz_library",
    "quiz_module",
    "quiz_knowledge_point",
    "quiz_question",
]


def upgrade():
    for table in _SOFT_DELETE_TABLES:
        op.add_column(table, sa.Column("deleted_at", sa.DateTime(timezone=True)))
        op.add_column(table, sa.Column("restore_until", sa.DateTime(timezone=True)))
        # 部分索引：列表查询 WHERE deleted_at IS NULL 走索引扫描
        op.create_index(
            f"ix_{table}_deleted_at", table, ["deleted_at"],
            postgresql_where=sa.text("deleted_at IS NULL"),
            sqlite_where=sa.text("deleted_at IS NULL"),
        )

    op.add_column("user", sa.Column("deactivated_at", sa.DateTime(timezone=True)))

    conn = op.get_bind()
    if conn.dialect.name == "sqlite":
        plus_30d = "datetime(deleted_at, '+30 days')"
    else:
        plus_30d = "deleted_at + INTERVAL '30 days'"
    for table in _QUIZ_SOFT_DELETE_TABLES:
        # 仅顺延仍在撤销期内的行；已过期的不复活撤销能力
        conn.execute(
            sa.text(
                f"UPDATE {table} SET restore_until = {plus_30d} "
                "WHERE status = 'deleted' AND deleted_at IS NOT NULL "
                "AND restore_until IS NOT NULL "
                "AND restore_until > CURRENT_TIMESTAMP "
                f"AND restore_until < {plus_30d}"
            )
        )


def downgrade():
    for table in _SOFT_DELETE_TABLES:
        op.drop_index(f"ix_{table}_deleted_at", table_name=table)
    op.drop_column("user", "deactivated_at")
    for table in _SOFT_DELETE_TABLES:
        op.drop_column(table, "restore_until")
        op.drop_column(table, "deleted_at")
