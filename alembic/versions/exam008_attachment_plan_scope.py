"""Allow batch-scoped certification attachments.

Revision ID: exam008
Revises: exam007
"""
import os

from alembic import op
import sqlalchemy as sa


revision = "exam008"
down_revision = "exam007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cert_attachment",
        sa.Column(
            "plan_id",
            sa.Integer(),
            sa.ForeignKey("plan.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_cert_attachment_scope",
        "cert_attachment",
        "plan_id IS NULL OR schedule_id IS NULL",
    )
    op.create_index(
        "ix_cert_attachment_vendor_plan",
        "cert_attachment",
        ["vendor", "plan_id", "is_active"],
    )


def downgrade() -> None:
    allow_destructive = os.getenv("ALLOW_DESTRUCTIVE_EXAM_DOWNGRADE") == "1"
    if op.get_context().as_sql:
        # 离线 SQL 也必须 fail-closed：有批次级附件时由数据库直接拒绝执行。
        if not allow_destructive:
            op.execute(
                "DO $$ BEGIN "
                "IF (SELECT count(*) FROM cert_attachment WHERE plan_id IS NOT NULL) > 0 "
                "THEN RAISE EXCEPTION "
                "'exam008 downgrade would convert batch attachments into vendor-level rows'; "
                "END IF; END $$;"
            )
    else:
        plan_scoped = op.get_bind().execute(
            sa.text(
                "SELECT count(*) FROM cert_attachment WHERE plan_id IS NOT NULL"
            )
        ).scalar_one()
        if plan_scoped and not allow_destructive:
            raise RuntimeError(
                f"exam008 downgrade would convert {plan_scoped} batch attachments "
                "into vendor-level attachments; set "
                "ALLOW_DESTRUCTIVE_EXAM_DOWNGRADE=1 after exporting them"
            )
    op.drop_index("ix_cert_attachment_vendor_plan", table_name="cert_attachment")
    op.drop_constraint(
        "ck_cert_attachment_scope", "cert_attachment", type_="check"
    )
    op.drop_column("cert_attachment", "plan_id")
