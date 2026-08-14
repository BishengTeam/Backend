"""add fixed-hierarchy V2 exam scope and revision snapshots

Revision ID: quiz006
Revises: quiz005
Create Date: 2026-08-13 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "quiz006"
down_revision: str | Sequence[str] | None = "quiz005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "quiz_exam", sa.Column("library_id", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "quiz_exam", sa.Column("scope_type", sa.String(length=24), nullable=True)
    )
    op.add_column(
        "quiz_exam", sa.Column("scope_id", sa.BigInteger(), nullable=True)
    )
    op.create_foreign_key(
        "fk_quiz_exam_library",
        "quiz_exam",
        "quiz_library",
        ["library_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.alter_column(
        "quiz_exam",
        "category_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )
    op.create_check_constraint(
        "ck_quiz_exam_scope_type",
        "quiz_exam",
        "scope_type IS NULL OR scope_type IN "
        "('library', 'module', 'knowledge_point')",
    )
    op.create_check_constraint(
        "ck_quiz_exam_scope",
        "quiz_exam",
        "((scope_type IS NULL AND scope_id IS NULL AND library_id IS NULL "
        "AND category_id IS NOT NULL) OR "
        "(scope_type IS NOT NULL AND scope_id IS NOT NULL "
        "AND library_id IS NOT NULL AND category_id IS NULL))",
    )
    op.create_index(
        "ix_quiz_exam_scope_history",
        "quiz_exam",
        ["user_id", "library_id", "scope_type", "scope_id", "started_at", "id"],
    )

    op.add_column(
        "quiz_exam_question",
        sa.Column("question_revision_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_quiz_exam_question_revision_question",
        "quiz_exam_question",
        "quiz_question_revision",
        ["question_revision_id", "question_id"],
        ["id", "question_id"],
        ondelete="RESTRICT",
    )
    op.alter_column(
        "quiz_exam_question",
        "category_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    has_v2_exams = bind.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM quiz_exam WHERE scope_type IS NOT NULL)")
    ).scalar()
    if has_v2_exams:
        raise RuntimeError(
            "quiz006 contains V2 exam snapshots without a legacy category; "
            "remove them only after verifying a backup before downgrade"
        )

    op.alter_column(
        "quiz_exam_question",
        "category_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    op.drop_constraint(
        "fk_quiz_exam_question_revision_question",
        "quiz_exam_question",
        type_="foreignkey",
    )
    op.drop_column("quiz_exam_question", "question_revision_id")

    op.drop_index("ix_quiz_exam_scope_history", table_name="quiz_exam")
    op.drop_constraint("ck_quiz_exam_scope", "quiz_exam", type_="check")
    op.drop_constraint("ck_quiz_exam_scope_type", "quiz_exam", type_="check")
    op.alter_column(
        "quiz_exam",
        "category_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    op.drop_constraint("fk_quiz_exam_library", "quiz_exam", type_="foreignkey")
    op.drop_column("quiz_exam", "scope_id")
    op.drop_column("quiz_exam", "scope_type")
    op.drop_column("quiz_exam", "library_id")
