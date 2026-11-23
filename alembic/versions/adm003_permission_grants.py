"""Normalize per-admin permission overrides to coarse grants.

后台权限目录收敛为「大类」后，``admin_user.permissions`` 里存量保存的细粒度
权限串需要按映射改写成大类授权（如 ``h3c:review`` → ``cert:h3c``）。
NULL 保持不变（继续跟随角色预设）。

Revision ID: adm003
Revises: exam012
"""
from alembic import op
import sqlalchemy as sa


revision = "adm003"
down_revision = "exam012"
branch_labels = None
depends_on = None


# 大类 → 细粒度（与 app/policy/permissions.py 的 PERMISSION_GRANTS 保持一致；
# 迁移内联副本，避免历史迁移随业务代码漂移）。
PERMISSION_GRANTS: dict[str, tuple[str, ...]] = {
    "dashboard:view": ("dashboard:view",),
    "user:manage": ("user:list", "user:write", "user:delete"),
    "order:manage": ("order:list", "order:write"),
    "content:manage": ("content:read", "content:list", "content:write", "document:read", "document:write"),
    "homepage:manage": ("homepage:list", "homepage:write"),
    "quiz:manage": (
        "quiz:list", "quiz:write", "quiz:import", "quiz_content_edit",
        "quiz_content_publish", "quiz_library_manage", "quiz_review", "course_quiz_bind",
    ),
    "course:manage": ("course:read", "course:write", "course:publish"),
    "classroom:manage": ("classroom:manage",),
    "cert:h3c": (
        "h3c:batch_manage", "h3c:review", "h3c:export",
        "h3c:refund", "h3c:order_close", "h3c:final_review",
    ),
    "cert:nisp": (
        "nisp:batch_manage", "nisp:review", "nisp:export",
        "nisp:refund", "nisp:final_review",
    ),
    "cert:renshe": ("renshe:review", "renshe:export"),
    "cert:sangfor": ("sangfor:review", "sangfor:export"),
    "competition:manage": ("competition:list", "competition:write", "activity:list", "activity:write"),
    "training:manage": ("training:list", "training:write", "job:list", "job:write"),
    "offline:manage": ("offline:view", "offline:confirm", "offline:refund", "offline:return", "offline:settings"),
    "points:manage": ("points:manage",),
}

LEGACY_TO_GRANT: dict[str, str] = {
    permission: grant
    for grant, permissions in PERMISSION_GRANTS.items()
    for permission in permissions
    if permission != grant
}


def normalize(values) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        grant = value if value in PERMISSION_GRANTS else LEGACY_TO_GRANT.get(value)
        if grant is None or grant in normalized:
            continue
        normalized.append(grant)
    return normalized


def upgrade() -> None:
    if op.get_context().as_sql:
        return
    admin_user = sa.table(
        "admin_user",
        sa.column("id", sa.Integer),
        sa.column("permissions", sa.JSON),
    )
    conn = op.get_bind()
    rows = conn.execute(
        sa.select(admin_user.c.id, admin_user.c.permissions).where(
            admin_user.c.permissions.is_not(None)
        )
    ).fetchall()
    for admin_id, permissions in rows:
        normalized = normalize(permissions)
        if normalized == list(permissions):
            continue
        conn.execute(
            admin_user.update()
            .where(admin_user.c.id == admin_id)
            .values(permissions=normalized)
        )


def downgrade() -> None:
    # 大类无法无损还原为原先的细粒度组合，保持不改写。
    return
