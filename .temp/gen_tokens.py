"""生成测试用户和管理员 token"""
import os, sys, logging, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET", "c7dedee911898aa98d78347653aa235eb8f3d539e2ad58db828ad2458b4543ae")
os.environ.setdefault("DB_PASSWORD", "wemini_pass")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "wemini")

for name in ["sqlalchemy.engine", "asyncio", "passlib", "slowapi"]:
    logging.getLogger(name).setLevel(logging.ERROR)

async def main():
    from app.adapter.database import async_session_factory
    from app.adapter.security import create_access_token, create_admin_access_token
    from app.domain.user.src.index import User, AdminUser
    from sqlalchemy import select

    async with async_session_factory() as db:
        u = (await db.execute(
            select(User).where(User.openid == "test_openid_user_001")
        )).scalar_one()
        user_token = create_access_token(u.id, u.openid)
        print(f"USER_TOKEN={user_token}")

        a = (await db.execute(
            select(AdminUser).where(AdminUser.username == "admin")
        )).scalar_one()
        admin_token = create_admin_access_token(a.id, a.username, a.role)
        print(f"ADMIN_TOKEN={admin_token}")

asyncio.run(main())
