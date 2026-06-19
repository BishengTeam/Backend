import asyncio, sys
sys.path.insert(0, "/home/bisheng/work/weMiniApp/Backend")
from sqlalchemy import select
from app.adapter.database import async_session_factory
from app.adapter.security import create_access_token
from app.domain.user.src.index import User

async def main():
    async with async_session_factory() as db:
        result = await db.execute(select(User).where(User.openid == "test_openid_user_001"))
        user = result.scalars().first()
        if user:
            print(create_access_token(user.id, user.openid))
        else:
            print("NO_USER", file=sys.stderr)
            sys.exit(1)

asyncio.run(main())
