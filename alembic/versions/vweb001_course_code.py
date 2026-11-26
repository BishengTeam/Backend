"""videoweb consolidation: course_code table, enrollment source columns, data migration

Revision ID: vweb001_course_code
Revises: cls004_classroom_recycle_bin

把视频站的兑换码 / 授权 / 进度收编回 Backend：
1) 新建 course_code（码归 Backend 管，本人绑定）
2) course_enrollment 增加 source / course_code_id 溯源列
3) 迁移 vw_course_code / vw_course_entitlement / vw_chapter_progress 存量数据
4) 视频站笔记与播放事件的 user_id 语义从 vw_user.id 改为 user.id

数据迁移依赖现网 vw_* 表：全新库（CI / 本地集成测试）不存在这些表，自动跳过；
离线 SQL 生成（--offline-sql）只输出 DDL，同样跳过数据段。
"""
from alembic import op
import sqlalchemy as sa

revision = "vweb001_course_code"
down_revision = "cls004_classroom_recycle_bin"
branch_labels = None
depends_on = None


CODE_MIGRATION_SQL = """
CREATE TEMP TABLE _vw_code_map (vw_id BIGINT PRIMARY KEY, new_id BIGINT NOT NULL) ON COMMIT DROP;

INSERT INTO course_code (
    code_hash, code_cipher, course_id, owner_user_id, status, source, note,
    created_by, redeemed_at, revoked_at, revoke_reason, created_at, updated_at
)
SELECT
    v.code_hash,
    v.code_cipher,
    v.course_id,
    COALESCE(v.owner_miniapp_user_id, u.miniapp_user_id) AS owner_user_id,
    CASE
        WHEN v.status = 'issued'
             AND COALESCE(v.owner_miniapp_user_id, u.miniapp_user_id) IS NULL
        THEN 'revoked'
        ELSE v.status
    END AS status,
    'migration',
    v.note,
    v.created_by,
    v.redeemed_at,
    CASE
        WHEN v.status = 'issued'
             AND COALESCE(v.owner_miniapp_user_id, u.miniapp_user_id) IS NULL
        THEN now()
        ELSE v.revoked_at
    END AS revoked_at,
    CASE
        WHEN v.status = 'issued'
             AND COALESCE(v.owner_miniapp_user_id, u.miniapp_user_id) IS NULL
        THEN 'code_migration_no_owner'
        ELSE NULL
    END AS revoke_reason,
    v.created_at,
    v.updated_at
FROM vw_course_code v
LEFT JOIN vw_user u ON u.id = v.redeemed_by
WHERE NOT EXISTS (
    SELECT 1 FROM course_code c WHERE c.code_hash = v.code_hash
);

INSERT INTO _vw_code_map (vw_id, new_id)
SELECT v.id, c.id
FROM vw_course_code v
JOIN course_code c ON c.code_hash = v.code_hash;

-- 授权：active -> enrolled + learning_access；其余（refunded 等）-> refunded + 回收
INSERT INTO course_enrollment (
    user_id, course_id, order_id, points_virtual_order_id, status, learning_access,
    access_granted_at, access_revoked_at, access_expires_at, source, course_code_id,
    created_at, updated_at
)
SELECT
    u.miniapp_user_id,
    e.course_id,
    NULL,
    NULL,
    CASE WHEN e.status = 'active' THEN 'enrolled' ELSE 'refunded' END,
    CASE WHEN e.status = 'active' THEN true ELSE false END,
    e.granted_at,
    e.revoked_at,
    NULL,
    'redeem',
    m.new_id,
    e.created_at,
    e.updated_at
FROM vw_course_entitlement e
JOIN vw_user u ON u.id = e.user_id
JOIN _vw_code_map m ON m.vw_id = e.course_code_id
WHERE u.miniapp_user_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM course_enrollment c
      WHERE c.user_id = u.miniapp_user_id
        AND c.course_id = e.course_id
        AND c.status IN ('pending_payment', 'enrolled', 'completed')
  );

-- 进度合并：同用户同章节取较大播放位置，完成态取 OR
INSERT INTO user_chapter_progress (
    user_id, course_id, chapter_id, last_position_seconds, is_completed, created_at, updated_at
)
SELECT
    u.miniapp_user_id,
    p.course_id,
    p.chapter_id,
    COALESCE(p.last_position_seconds, 0),
    COALESCE(p.is_completed, false),
    p.created_at,
    p.updated_at
FROM vw_chapter_progress p
JOIN vw_user u ON u.id = p.user_id
WHERE u.miniapp_user_id IS NOT NULL
ON CONFLICT (user_id, course_id, chapter_id) DO UPDATE
SET last_position_seconds = GREATEST(
        user_chapter_progress.last_position_seconds,
        EXCLUDED.last_position_seconds
    ),
    is_completed = user_chapter_progress.is_completed OR EXCLUDED.is_completed,
    updated_at = GREATEST(user_chapter_progress.updated_at, EXCLUDED.updated_at);

-- 保留表（笔记 / 播放事件）的 user_id 语义切换为 user.id
UPDATE vw_chapter_note n
SET user_id = u.miniapp_user_id
FROM vw_user u
WHERE u.id = n.user_id AND u.miniapp_user_id IS NOT NULL;

UPDATE vw_playback_event e
SET user_id = u.miniapp_user_id
FROM vw_user u
WHERE u.id = e.user_id AND u.miniapp_user_id IS NOT NULL;
"""


def _legacy_tables_present(bind) -> bool:
    return bind.execute(sa.text("SELECT to_regclass('vw_course_code')")).scalar() is not None


def upgrade() -> None:
    op.create_table(
        "course_code",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("code_hash", sa.String(length=128), nullable=False),
        sa.Column("code_cipher", sa.String(length=512), nullable=False),
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("course.id"), nullable=False),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("user.id"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="issued"),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="admin"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(length=128), nullable=True),
        sa.Column("revoked_by", sa.Integer(), sa.ForeignKey("admin_user.id"), nullable=True),
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
        sa.UniqueConstraint("code_hash", name="uq_course_code_hash"),
        sa.CheckConstraint(
            "status IN ('issued', 'redeemed', 'revoked', 'refunded')",
            name="ck_course_code_status",
        ),
        sa.CheckConstraint(
            "status IN ('revoked', 'refunded') OR owner_user_id IS NOT NULL",
            name="ck_course_code_owner",
        ),
    )
    op.create_index("ix_course_code_owner", "course_code", ["owner_user_id"])
    op.create_index("ix_course_code_course_status", "course_code", ["course_id", "status"])

    op.add_column("course_enrollment", sa.Column("source", sa.String(length=16), nullable=True))
    op.add_column("course_enrollment", sa.Column("course_code_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_course_enrollment_course_code",
        "course_enrollment",
        "course_code",
        ["course_code_id"],
        ["id"],
    )
    op.create_index(
        "ix_course_enrollment_course_code", "course_enrollment", ["course_code_id"]
    )

    if op.get_context().as_sql:
        return
    bind = op.get_bind()
    if not _legacy_tables_present(bind):
        return
    bind.execute(sa.text(CODE_MIGRATION_SQL))


def downgrade() -> None:
    op.drop_index("ix_course_enrollment_course_code", table_name="course_enrollment")
    op.drop_constraint(
        "fk_course_enrollment_course_code", "course_enrollment", type_="foreignkey"
    )
    op.drop_column("course_enrollment", "course_code_id")
    op.drop_column("course_enrollment", "source")
    op.drop_index("ix_course_code_course_status", table_name="course_code")
    op.drop_index("ix_course_code_owner", table_name="course_code")
    op.drop_table("course_code")
