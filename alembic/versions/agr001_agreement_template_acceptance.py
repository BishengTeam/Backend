"""agreement template and acceptance tables (P0 e-agreement)

Revision ID: agr001
Revises: cls003_classroom_quiz_attachment
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "agr001"
down_revision: str | Sequence[str] = "cls003_classroom_quiz_attachment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agreement_template",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "type IN ('user_terms', 'privacy', 'identity_auth')",
            name="ck_agreement_template_type",
        ),
        sa.CheckConstraint("version > 0", name="ck_agreement_template_version"),
        sa.CheckConstraint(
            "status IN ('active', 'archived')", name="ck_agreement_template_status"
        ),
        sa.CheckConstraint("length(title) > 0", name="ck_agreement_template_title"),
        sa.CheckConstraint("length(content) > 0", name="ck_agreement_template_content"),
    )
    op.create_index("ix_agreement_template_type", "agreement_template", ["type"])
    op.create_index(
        "uq_agreement_template_active_type",
        "agreement_template",
        ["type"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "agreement_acceptance",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_snapshot", sa.Text(), nullable=False),
        sa.Column(
            "accepted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["template_id"], ["agreement_template.id"]),
        sa.CheckConstraint("version > 0", name="ck_agreement_acceptance_version"),
        sa.UniqueConstraint(
            "user_id", "template_id", name="uq_agreement_acceptance_user_template"
        ),
    )
    op.create_index("ix_agreement_acceptance_user", "agreement_acceptance", ["user_id"])
    op.create_index(
        "ix_agreement_acceptance_template", "agreement_acceptance", ["template_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_agreement_acceptance_template", table_name="agreement_acceptance")
    op.drop_index("ix_agreement_acceptance_user", table_name="agreement_acceptance")
    op.drop_table("agreement_acceptance")
    op.drop_index("uq_agreement_template_active_type", table_name="agreement_template")
    op.drop_index("ix_agreement_template_type", table_name="agreement_template")
    op.drop_table("agreement_template")
