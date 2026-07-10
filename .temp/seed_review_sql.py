"""直接向 review 表写入多样化的测试审核记录"""
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
from app.domain.review.src.index import Review
from datetime import datetime, timedelta, timezone

async def seed():
    now = datetime.now(timezone.utc)
    records = [
        Review(target_type="identity", target_id=338, reviewer_id=1,
               action="approve", comment=None,
               created_at=now - timedelta(days=3),
               updated_at=now - timedelta(days=3)),
        Review(target_type="identity", target_id=339, reviewer_id=1,
               action="reject", comment="身份证照片模糊，请重新上传",
               created_at=now - timedelta(days=2),
               updated_at=now - timedelta(days=2)),
        Review(target_type="student", target_id=338, reviewer_id=1,
               action="approve", comment=None,
               created_at=now - timedelta(days=2, hours=1),
               updated_at=now - timedelta(days=2, hours=1)),
        Review(target_type="enterprise", target_id=340, reviewer_id=1,
               action="reject", comment="营业执照已过期",
               created_at=now - timedelta(days=1),
               updated_at=now - timedelta(days=1)),
        Review(target_type="order", target_id=171, reviewer_id=1,
               action="reject", comment="考生信息与实名不匹配",
               created_at=now - timedelta(hours=6),
               updated_at=now - timedelta(hours=6)),
        Review(target_type="order", target_id=172, reviewer_id=1,
               action="approve", comment=None,
               created_at=now - timedelta(hours=4),
               updated_at=now - timedelta(hours=4)),
        Review(target_type="identity", target_id=341, reviewer_id=1,
               action="approve", comment=None,
               created_at=now - timedelta(hours=2),
               updated_at=now - timedelta(hours=2)),
    ]
    async with get_db_ctx() as db:
        db.add_all(records)
        await db.commit()
        print(f"写入 {len(records)} 条审核记录")

asyncio.run(seed())
