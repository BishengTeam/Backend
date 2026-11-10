"""Allow draft (unpublished) quiz libraries to enter the recycle bin."""

from alembic import op
import sqlalchemy as sa

revision = "quiz021"
down_revision = "cp020_gzh_article_admin"
branch_labels = None
depends_on = None

# quiz021 前的旧约束：deleted 分支强制 archived_at 非空，draft 无法直接进回收站。
_OLD_CONSTRAINT = (
    "((status = 'draft' AND published_at IS NULL AND suspended_at IS NULL "
    "AND archived_at IS NULL AND deleted_at IS NULL) OR "
    "(status = 'published' AND published_at IS NOT NULL "
    "AND suspended_at IS NULL AND archived_at IS NULL AND deleted_at IS NULL) OR "
    "(status = 'suspended' AND published_at IS NOT NULL "
    "AND suspended_at IS NOT NULL AND archived_at IS NULL AND deleted_at IS NULL) OR "
    "(status = 'archived' AND archived_at IS NOT NULL AND deleted_at IS NULL) OR "
    "(status = 'deleted' AND archived_at IS NOT NULL AND deleted_at IS NOT NULL))"
)
# 放宽后：deleted 仅要求 deleted_at 非空（draft 删除的行 archived_at 保持 NULL）。
_NEW_CONSTRAINT = (
    "((status = 'draft' AND published_at IS NULL AND suspended_at IS NULL "
    "AND archived_at IS NULL AND deleted_at IS NULL) OR "
    "(status = 'published' AND published_at IS NOT NULL "
    "AND suspended_at IS NULL AND archived_at IS NULL AND deleted_at IS NULL) OR "
    "(status = 'suspended' AND published_at IS NOT NULL "
    "AND suspended_at IS NOT NULL AND archived_at IS NULL AND deleted_at IS NULL) OR "
    "(status = 'archived' AND archived_at IS NOT NULL AND deleted_at IS NULL) OR "
    "(status = 'deleted' AND deleted_at IS NOT NULL))"
)


def upgrade():
    op.drop_constraint("ck_quiz_library_lifecycle", "quiz_library", type_="check")
    op.create_check_constraint(
        "ck_quiz_library_lifecycle", "quiz_library", _NEW_CONSTRAINT
    )


def downgrade():
    # 若存在删自草稿的回收站行（archived_at 为空），需先恢复或彻底删除它们，
    # 否则旧约束无法通过。
    if op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM quiz_library "
            "WHERE status = 'deleted' AND archived_at IS NULL LIMIT 1"
        )
    ).scalar():
        raise RuntimeError(
            "存在删自草稿的回收站题库，请先恢复或彻底删除后再回退该约束"
        )
    op.drop_constraint("ck_quiz_library_lifecycle", "quiz_library", type_="check")
    op.create_check_constraint(
        "ck_quiz_library_lifecycle", "quiz_library", _OLD_CONSTRAINT
    )
