"""开发环境快速登录：为指定测试用户生成 JWT access_token

用法：
    python scripts/dev_login.py                           # 默认登录 test_openid_user_001
    python scripts/dev_login.py test_openid_user_002      # 指定用户
    python scripts/dev_login.py test_openid_user_001 --admin  # 生成管理员 token

说明：
    - 直接通过数据库查询用户（或管理员），生成 access_token 和 curl 示例
    - 使用当前 .env 中的 JWT_SECRET
    - 绕过微信登录流程，适合开发调试
"""

import asyncio
import sys

from sqlalchemy import select

from app.core.database import async_session_factory
from app.core.security import create_access_token, create_admin_access_token
from app.domain.user.src.index import AdminUser, User


async def main():
    openid = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "test_openid_user_001"
    is_admin = "--admin" in sys.argv

    if is_admin:
        async with async_session_factory() as db:
            result = await db.execute(
                select(AdminUser).where(AdminUser.username == openid)
            )
            admin = result.scalars().first()
            if not admin:
                print(f"❌ 管理员不存在: {openid}")
                print("  可用用户名: admin, editor, cs, finance, auditor")
                return
            token = create_admin_access_token(admin.id, admin.username, admin.role)
            print(f"✅ 管理员 Token ({admin.username} | {admin.role})")
            print(f"   admin_id: {admin.id}")
    else:
        async with async_session_factory() as db:
            result = await db.execute(
                select(User).where(User.openid == openid)
            )
            user = result.scalars().first()
            if not user:
                print(f"❌ 用户不存在: {openid}")
                print("  可用 openid: test_openid_user_001 ~ 005")
                return
            token = create_access_token(user.id, user.openid)
            print(f"✅ 用户 Token ({user.openid})")
            print(f"   user_id: {user.id}")
            print(f"   is_active: {user.is_active}")

    print(f"\n{'─' * 50}")
    print(f"Token:\n{token}")
    print(f"{'─' * 50}")
    print(f"\n# curl 测试:")
    if is_admin:
        print(f'curl -H "Authorization: Bearer {token}" http://localhost:8000/admin/users')
    else:
        print(f'curl -H "Authorization: Bearer {token}" http://localhost:8000/api/user/profile')
        print(f'curl -H "Authorization: Bearer {token}" http://localhost:8000/api/courses')


if __name__ == "__main__":
    asyncio.run(main())
