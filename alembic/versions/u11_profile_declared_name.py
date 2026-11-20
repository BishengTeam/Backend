"""Add user_profile.declared_name for practice/exam name attribution (u11).

甲方口径（2026-11-19）：学生进入练习/考试前自填一次姓名，后台按姓名统计
做题与考试数据。该字段为用户自填（无需审核），与实名认证的
``user_identity.real_name`` 分开存放，避免污染实名状态。
"""

from alembic import op
import sqlalchemy as sa

revision = "u11"
down_revision = "adm002"  # 接在当前唯一 head 之后，避免新增分支 head
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user_profile",
        sa.Column("declared_name", sa.String(length=32), nullable=True),
    )


def downgrade():
    # 历史库可能在迁移图线性化前就已越过 exam004，导致本迁移从未执行。
    # 降级时若列不存在必须保持幂等，否则整个降级链会被卡死。
    if op.get_context().as_sql:
        return
    exists = op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'user_profile' AND column_name = 'declared_name'"
        )
    ).scalar()
    if exists:
        op.drop_column("user_profile", "declared_name")
