import asyncio, sys
sys.path.insert(0, '.')
from app.core.database import async_session_factory
from sqlalchemy import text

async def main():
    async with async_session_factory() as db:
        result = await db.execute(text('SELECT id, name, vendor FROM certification'))
        for row in result:
            print(f'id={row[0]} name={row[1]} vendor="{row[2]}"')

asyncio.run(main())
