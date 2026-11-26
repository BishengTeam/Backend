"""videoweb consolidation: move chapter notes to user_chapter_note

Revision ID: vweb003_user_note
Revises: r60001, vweb002_user_access

章节学习笔记收编（2026-11-26）：从视频站保留表 vw_chapter_note 迁到 Backend 自有
user_chapter_note，站点前端改调 /api/videoweb/site/...（不再依赖视频站旧会话）。

vweb001 已把 vw_chapter_note.user_id 的语义从 vw_user.id 改写为 user.id，
因此这里直接按 user.id 复制，无需再关联 vw_user。
全新库（CI / 本地集成测试）没有 vw_chapter_note，自动跳过数据段。
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "vweb003_user_note"
down_revision: str | Sequence[str] = ("r60001", "vweb002_user_access")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


NOTE_MIGRATION_SQL = """
INSERT INTO user_chapter_note (
    user_id, course_id, chapter_id, resource_version, content, created_at, updated_at
)
SELECT
    n.user_id,
    n.course_id,
    n.chapter_id,
    COALESCE(n.resource_version, 1),
    COALESCE(n.content, ''),
    COALESCE(n.created_at, now()),
    COALESCE(n.updated_at, now())
FROM vw_chapter_note n
JOIN "user" u ON u.id = n.user_id
JOIN course c ON c.id = n.course_id
JOIN course_chapter cc ON cc.id = n.chapter_id
ON CONFLICT (user_id, course_id, chapter_id) DO NOTHING;
"""


def upgrade() -> None:
    op.create_table(
        "user_chapter_note",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("course.id"), nullable=False),
        sa.Column(
            "chapter_id",
            sa.Integer(),
            sa.ForeignKey("course_chapter.id"),
            nullable=False,
        ),
        sa.Column(
            "resource_version", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "user_id", "course_id", "chapter_id", name="uq_user_chapter_note"
        ),
    )
    op.create_index(
        "ix_user_chapter_note_user_id", "user_chapter_note", ["user_id"]
    )
    op.create_index(
        "ix_user_chapter_note_course_id", "user_chapter_note", ["course_id"]
    )

    if op.get_context().as_sql:
        return
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT to_regclass('vw_chapter_note')")).scalar() is None:
        return
    bind.execute(sa.text(NOTE_MIGRATION_SQL))


def downgrade() -> None:
    op.drop_index("ix_user_chapter_note_course_id", table_name="user_chapter_note")
    op.drop_index("ix_user_chapter_note_user_id", table_name="user_chapter_note")
    op.drop_table("user_chapter_note")
