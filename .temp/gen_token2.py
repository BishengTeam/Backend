import asyncio, sys, os
sys.path.insert(0, '/home/bisheng/work/weMiniApp/Backend')
os.environ['JWT_SECRET'] = 'c7dedee911898aa98d78347653aa235eb8f3d539e2ad58db828ad2458b4543ae'
os.environ['DB_USER'] = 'bisheng'
os.environ['DB_PASSWORD'] = 'bisheng6@6@6'
os.environ['DB_HOST'] = '127.0.0.1'
os.environ['DB_PORT'] = '3306'
os.environ['DB_NAME'] = 'wemini_app_dev'
os.environ['REDIS_URL'] = 'redis://127.0.0.1:6379/0'
from app.adapter.database import async_session_factory
from app.adapter.security import create_access_token
from app.domain.user.src.index import User
from sqlalchemy import select

async def main():
    async with async_session_factory() as db:
        u = (await db.execute(select(User).where(User.openid=='test_openid_user_001'))).scalar_one()
        print(create_access_token(u.id, u.openid))
asyncio.run(main())
