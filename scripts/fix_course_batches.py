"""修复 course.batches 字段中存储为 [] 的历史数据。

问题背景：
    早期管理端创建/更新课程时，batches 字段校验为 Any | None，导致部分数据被存成 JSON
    空数组 []。而小程序课程详情接口的 CourseDetailResponse 要求 batches 为 dict | None，
    于是 Pydantic 校验失败，抛出 500 "服务器内部错误"。

修复逻辑：
    把 course 表中 batches = '[]' 的行统一刷成 '{}'，与 schema 期望的字典类型保持一致。

运行方式（从仓库根目录）：
    python scripts/fix_course_batches.py          # 预览模式（不执行）
    python scripts/fix_course_batches.py --run    # 执行更新
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
os.environ.setdefault("JWT_SECRET", "fix-course-batches-placeholder-do-not-use-in-prod")

from sqlalchemy import String, func, select, update

from app.adapter.database import get_db_ctx
from app.domain.certification.src.index import Course


async def main(dry_run: bool = True) -> None:
    async with get_db_ctx() as db:
        # JSON 字段在不同数据库比较语法不同，统一 cast 成字符串比较最通用。
        bad_filter = func.coalesce(func.cast(Course.batches, String), "") == "[]"

        stmt = select(func.count(Course.id)).where(bad_filter)
        total_bad = (await db.execute(stmt)).scalar() or 0
        print(f"发现 course.batches == '[]' 的记录数: {total_bad}")

        if dry_run:
            print("当前为预览模式，未执行更新。追加 --run 后才会写入数据库。")
            return

        if total_bad == 0:
            print("无需更新。")
            return

        await db.execute(
            update(Course)
            .where(bad_filter)
            .values(batches={})
        )
        await db.commit()
        print(f"已更新 {total_bad} 条记录: course.batches 从 [] 改为 {{}}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="修复 course.batches 为 [] 的历史数据")
    parser.add_argument("--run", action="store_true", help="实际执行更新，否则仅预览")
    args = parser.parse_args()

    asyncio.run(main(dry_run=not args.run))
