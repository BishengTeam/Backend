"""创建默认管理员账号"""
import asyncio
import hashlib
import secrets

from sqlalchemy import select

from app.core.database import async_session_factory
from app.models.admin_user import AdminUser

SALT_LENGTH = 32
HASH_ITERATIONS = 600_000

DEFAULT_ADMINS = [
    {"username": "admin", "password": "admin123", "role": "super_admin"},
]


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SALT_LENGTH)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, HASH_ITERATIONS)
    return salt.hex() + ":" + dk.hex()


async def main():
    async with async_session_factory() as db:
        existing = (await db.execute(select(AdminUser).limit(1))).first()
        if existing:
            print("管理员已存在，跳过。")
            return

        print("创建默认管理员账号...")
        for item in DEFAULT_ADMINS:
            admin = AdminUser(
                username=item["username"],
                password_hash=hash_password(item["password"]),
                role=item["role"],
            )
            db.add(admin)
        await db.commit()
        print("  ✓ 管理员账号已创建 (username: admin, password: admin123)")
        print("  ⚠ 请登录后立即修改密码")


if __name__ == "__main__":
    asyncio.run(main())
