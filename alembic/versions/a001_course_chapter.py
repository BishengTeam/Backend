"""add course_chapter and user_chapter_progress

Revision ID: a001_course_chapter
Revises: 3fd0076eb0ce
Create Date: 2026-07-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a001_course_chapter'
down_revision: Union[str, Sequence[str], None] = '3fd0076eb0ce'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add course_chapter and user_chapter_progress tables, and free_preview_seconds to course."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # 1. course 表新增 free_preview_seconds 列
    existing_columns = [c["name"] for c in inspector.get_columns("course")]
    if "free_preview_seconds" not in existing_columns:
        op.add_column("course",
            sa.Column("free_preview_seconds", sa.Integer(), nullable=True)
        )

    # 2. 创建 course_chapter 表
    if "course_chapter" not in inspector.get_table_names():
        op.create_table("course_chapter",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("course_id", sa.Integer(), sa.ForeignKey("course.id"), nullable=False),
            sa.Column("title", sa.String(256), nullable=False),
            sa.Column("video_url", sa.String(512), nullable=True),
            sa.Column("duration", sa.Integer(), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("course_id", "sort_order", name="uq_course_chapter_sort"),
        )
        op.create_index(op.f("ix_course_chapter_course_id"), "course_chapter", ["course_id"])

    # 3. 创建 user_chapter_progress 表
    if "user_chapter_progress" not in inspector.get_table_names():
        op.create_table("user_chapter_progress",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
            sa.Column("course_id", sa.Integer(), sa.ForeignKey("course.id"), nullable=False),
            sa.Column("chapter_id", sa.Integer(), sa.ForeignKey("course_chapter.id"), nullable=False),
            sa.Column("last_position_seconds", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_completed", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "course_id", "chapter_id", name="uq_user_chapter_progress"),
        )
        op.create_index(op.f("ix_user_chapter_progress_user_id"), "user_chapter_progress", ["user_id"])
        op.create_index(op.f("ix_user_chapter_progress_course_id"), "user_chapter_progress", ["course_id"])


def downgrade() -> None:
    """Remove course_chapter and user_chapter_progress tables, and free_preview_seconds column."""
    op.drop_table("user_chapter_progress")
    op.drop_table("course_chapter")
    op.drop_column("course", "free_preview_seconds")
