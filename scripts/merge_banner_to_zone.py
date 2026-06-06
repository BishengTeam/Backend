"""将 banner 表数据迁移到 zone 表"""
import asyncio
from sqlalchemy import text
from app.adapter.database import get_db_ctx


async def migrate():
    async with get_db_ctx() as db:
        banners = (await db.execute(text("SELECT * FROM banner"))).all()
        for b in banners:
            await db.execute(
                text(
                    "INSERT INTO zone (zone_type, title, cover_url, link_url, sort_order, "
                    "is_active, is_banner, start_time, end_time, created_at, updated_at) "
                    "VALUES (:type, :title, :url, :link, :sort, :active, true, "
                    ":start, :end, now(), now())"
                ),
                {
                    "type": "banner",
                    "title": f"Banner_{getattr(b, 'id', 0)}",
                    "url": getattr(b, "image_url", ""),
                    "link": getattr(b, "jump_link", None),
                    "sort": getattr(b, "sort", 0),
                    "active": getattr(b, "is_active", True),
                    "start": getattr(b, "start_time", None),
                    "end": getattr(b, "end_time", None),
                },
            )
        await db.commit()
        print(f"Migrated {len(banners)} banners to zone table")


if __name__ == "__main__":
    asyncio.run(migrate())
