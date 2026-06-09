import asyncio
import sys, os
sys.path.insert(0, '.')
os.environ.setdefault('JWT_SECRET', 'smoke-minimum-32-chars-key-here!')
os.environ['APP_ENV'] = 'test'

# 必须在导入 app 模块之前抑制日志
import logging
logging.basicConfig(level=logging.WARNING, force=True)

from app.adapter.database import async_session_factory
from app.domain.user.src.index import User
from sqlalchemy import select
from app.adapter.security import create_access_token

async def main():
    async with async_session_factory() as db:
        u = (await db.execute(select(User).where(User.openid == 'test_openid_user_001'))).scalar_one()
        token = create_access_token(u.id, u.openid)
        with open('.temp/token.txt', 'w') as f:
            f.write(token)

asyncio.run(main())
