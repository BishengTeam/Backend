import asyncio, sys
sys.path.insert(0, '.')
from app.core.database import async_session_factory
from sqlalchemy import text

async def main():
    async with async_session_factory() as db:
        result = await db.execute(text("DELETE FROM certification WHERE vendor = 'SmokeVendor'"))
        await db.commit()
        print(f"Deleted {result.rowcount} rows with vendor='SmokeVendor'")

asyncio.run(main())
