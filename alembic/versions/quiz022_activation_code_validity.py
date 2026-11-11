"""Switch activation-code validity to entitlement duration after redemption (quiz022).

甲方口径（2026-11-11）：激活码本身没有兑换有效期，效期自激活起算。
生成时存 ``validity_days``（NULL = 激活后永久），兑换时据其写权益 ``ends_at``；
旧的 ``expires_at``（兑换截止）语义废除，存量码按 ``expires_at - created_at``
折算天数回填后删除该列。
"""

from alembic import op
import sqlalchemy as sa

revision = "quiz022"
down_revision = "quiz021"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "quiz_activation_code",
        sa.Column("validity_days", sa.Integer(), nullable=True),
    )
    op.add_column(
        "quiz_activation_code",
        sa.Column(
            "is_custom",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # 生成时 expires_at = created_at + 整天（微秒已剥离），精确折算；
    # expires_at 为 NULL 的存量码保持 NULL，激活后永久。
    op.execute(
        "UPDATE quiz_activation_code SET validity_days = "
        "round(EXTRACT(EPOCH FROM (expires_at - created_at)) / 86400)::int "
        "WHERE expires_at IS NOT NULL"
    )
    op.drop_column("quiz_activation_code", "expires_at")


def downgrade():
    op.add_column(
        "quiz_activation_code",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE quiz_activation_code SET expires_at = "
        "created_at + validity_days * interval '1 day' "
        "WHERE validity_days IS NOT NULL"
    )
    op.drop_column("quiz_activation_code", "is_custom")
    op.drop_column("quiz_activation_code", "validity_days")
