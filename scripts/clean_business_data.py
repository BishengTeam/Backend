"""清空所有业务数据，保留核心配置。

运行方式（从仓库根目录）：
    python scripts/clean_business_data.py          # 预览模式（不执行）
    python scripts/clean_business_data.py --run    # 执行删除

保留的表：
    admin_user       — 管理员账号
    price_config     — 价格配置
    quick_question   — 快捷问题
    alembic_version  — 迁移版本（SQLAlchemy 内部表）
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# 注入缺失的环境变量，避免 Settings() 初始化失败。
# 此脚本只需要数据库连接，不需要 JWT / 微信等业务配置。
os.environ.setdefault("JWT_SECRET", "clean-script-placeholder-do-not-use-in-prod")


# ── 保留表清单 ────────────────────────────────────────────────────
KEEP_TABLES = frozenset({
    "admin_user",
    "price_config",
    "quick_question",
    "alembic_version",
})

# ── 删除顺序（外键依赖的拓扑排序 — 子表先删，父表后删）─────────
# 关键依赖：
#   inventory_record → order + inventory  （循环引用的一端）
#   order            → user + inventory
#   inventory        → 无 FK
#   user             ← 几乎所有表引用

DELETE_ORDER = [
    # ── 第 1 批：引用 user 的叶子表 ──
    "activity_reminder",
    "activity_registration",
    "agreement",
    "collection",
    "competition_reg",
    "conversation",
    "course_enrollment",
    "deleted_openid",
    "job_application",
    "points_history",
    # Quiz user/session data (children before their snapshots and parents).
    "quiz_checkin",
    "quiz_practice_attempt",
    "quiz_practice_session_question",
    "quiz_exam_answer",
    "quiz_exam_question",
    "quiz_wrong_item",
    "quiz_collection",
    "quiz_user_stats",
    "share",
    "ticket",
    "user_coupon",
    "user_profile",
    "user_realname",
    "user_student",
    "user_enterprise",
    "user_points",
    # H3C child records must be removed before orders and users.
    "h3c_export_item",
    "h3c_review",
    "h3c_refund_request",
    "h3c_material",
    "h3c_registration",
    "h3c_material_upload",

    # ── 第 2 批：inventory_record（解除 order ↔ inventory 循环引用）──
    "inventory_record",

    # ── 第 3 批：order（引用 user + inventory）──
    "order",

    # ── 第 4 批：独立业务父表 ──
    "activity",
    "banner",
    "certification",
    "coupon",
    "course_upload",
    "course",
    "inventory",
    "job",
    "quiz_question_stats",
    "quiz_import_error",
    "quiz_import_job",
    "quiz_admin_audit_log",
    "quiz_question",
    "quiz_practice_session",
    "quiz_exam",
    "training",
    "zone",
    "quiz_category",
    "h3c_export_job",
    "h3c_exam_batch",

    # ── 第 5 批：user（所有引用已解除）──
    "user",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="清空所有业务数据，保留核心配置表。",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="执行删除。不加此参数为预览模式。",
    )
    return parser


async def _preview(db, tables: list[str]) -> dict[str, int]:
    """预览各表行数。"""
    from sqlalchemy import text

    counts: dict[str, int] = {}
    print("预览模式 — 各表当前行数：\n")
    for t in tables:
        result = await db.execute(text(f"SELECT COUNT(*) FROM \"{t}\""))
        count = result.scalar() or 0
        counts[t] = count
        marker = " ← 保留" if t in KEEP_TABLES else ""
        print(f"  {t:28s} {count:>6d} 行{marker}")
    return counts


async def _execute(db, tables: list[str]) -> dict[str, int]:
    """执行删除。"""
    from sqlalchemy import text

    deleted: dict[str, int] = {}
    for t in tables:
        if t in KEEP_TABLES:
            continue
        result = await db.execute(text(f"DELETE FROM \"{t}\""))
        deleted[t] = result.rowcount
        print(f"  ✅ {t:28s} 已删除 {result.rowcount:>6d} 行")
    await db.commit()
    return deleted


async def run(args: argparse.Namespace) -> int:
    from app.adapter.database import get_db_ctx

    all_tables = list(KEEP_TABLES) + DELETE_ORDER

    async with get_db_ctx() as db:
        if not args.run:
            await _preview(db, all_tables)
            print("\n以上为预览。确认无误后加 --run 执行删除。")
            return 0

        print("执行删除...\n")
        deleted = await _execute(db, DELETE_ORDER)

    total = sum(deleted.values())
    print(f"\n完成：{len(deleted)} 个表，共删除 {total} 行。")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    asyncio.run(run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
