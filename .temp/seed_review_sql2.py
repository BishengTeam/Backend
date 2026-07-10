"""直接用 SQL 写入审核测试数据"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import asyncio

os.environ.setdefault("DB_USER", "bisheng")
os.environ.setdefault("DB_PASSWORD", "bisheng6@6@6")
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_PORT", "3306")
os.environ.setdefault("DB_NAME", "wemini_app_dev")
os.environ.setdefault("JWT_SECRET", "c7dedee911898aa98d78347653aa235eb8f3d539e2ad58db828ad2458b4543ae")

from app.adapter.database import get_db_ctx
from sqlalchemy import text
from datetime import datetime, timedelta, timezone

async def seed():
    now = datetime.now(timezone.utc)
    # 先查 admin_user id=1 是否存在
    async with get_db_ctx() as db:
        result = await db.execute(text("SELECT id FROM admin_user WHERE id = 1"))
        admin = result.scalar()
        reviewer_id = admin if admin else 1
        print(f"reviewer_id={reviewer_id}")

        records = [
            (reviewer_id, "identity", 338, "approve", None,
             now - timedelta(days=3), now - timedelta(days=3)),
            (reviewer_id, "identity", 339, "reject", "身份证照片模糊，请重新上传",
             now - timedelta(days=2), now - timedelta(days=2)),
            (reviewer_id, "student", 338, "approve", None,
             now - timedelta(days=2, hours=1), now - timedelta(days=2, hours=1)),
            (reviewer_id, "enterprise", 340, "reject", "营业执照已过期",
             now - timedelta(days=1), now - timedelta(days=1)),
            (reviewer_id, "order", 171, "reject", "考生信息与实名不匹配",
             now - timedelta(hours=6), now - timedelta(hours=6)),
            (reviewer_id, "order", 172, "approve", None,
             now - timedelta(hours=4), now - timedelta(hours=4)),
            (reviewer_id, "identity", 341, "approve", None,
             now - timedelta(hours=2), now - timedelta(hours=2)),
        ]

        for r in records:
            await db.execute(text(
                "INSERT INTO review (reviewer_id, target_type, target_id, action, comment, created_at, updated_at) "
                "VALUES (:reviewer_id, :target_type, :target_id, :action, :comment, :created_at, :updated_at)"
            ), dict(zip(
                ["reviewer_id", "target_type", "target_id", "action", "comment", "created_at", "updated_at"], r
            )))
        await db.commit()
        print(f"写入 {len(records)} 条审核记录")

asyncio.run(seed())
