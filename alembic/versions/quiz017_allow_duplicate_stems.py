"""allow duplicate stems in one library: unique stem index becomes a plain index

Revision ID: quiz017
Revises: rsh008_review_requirements
Create Date: 2026-10-22 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "quiz017"
down_revision: str | Sequence[str] | None = "rsh008_review_requirements"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 2026-10-22 口径：题库导入行行入库（源文件多少行就建多少题），
    # 同题干允许共存；库级题干唯一索引退化为普通索引，保留 hash 查询性能。
    op.drop_index("uq_quiz_question_library_text_hash", table_name="quiz_question")
    op.create_index(
        "ix_quiz_question_library_text_hash",
        "quiz_question",
        ["library_id", "question_text_hash"],
        unique=False,
        postgresql_where=sa.text("library_id IS NOT NULL AND stem_reserved = true"),
    )


def downgrade() -> None:
    # 注意：若库中已存在同题干的多道题，重建唯一索引会直接失败——
    # 这是有意的回滚保护（回滚前必须先人工清重）。
    op.drop_index("ix_quiz_question_library_text_hash", table_name="quiz_question")
    op.create_index(
        "uq_quiz_question_library_text_hash",
        "quiz_question",
        ["library_id", "question_text_hash"],
        unique=True,
        postgresql_where=sa.text("library_id IS NOT NULL AND stem_reserved = true"),
    )
