"""Add a first-redemption deadline for newly issued quiz activation codes."""

from alembic import op
import sqlalchemy as sa

revision = "quiz020"
down_revision = "quiz019"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("quiz_activation_code", sa.Column("expires_at", sa.DateTime(timezone=True)))


def downgrade():
    if op.get_bind().execute(sa.text(
        "SELECT 1 FROM quiz_activation_code WHERE expires_at IS NOT NULL LIMIT 1"
    )).scalar():
        raise RuntimeError("有已发放的限期题库激活码，不能移除兑换截止时间")
    op.drop_column("quiz_activation_code", "expires_at")
