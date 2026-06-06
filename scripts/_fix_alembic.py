import asyncio
from app.adapter.database import get_db_ctx
from sqlalchemy import text

async def fix():
    async with get_db_ctx() as db:
        await db.execute(text("DELETE FROM alembic_version"))
        await db.execute(text("INSERT INTO alembic_version VALUES ('b0c1d2e3f4a5')"))
        await db.commit()
        print('Fixed')

asyncio.run(fix())
