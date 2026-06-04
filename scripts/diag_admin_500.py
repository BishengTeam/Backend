#!/usr/bin/env python3
"""诊断管理后台 500 错误"""
import sys
sys.path.insert(0, '/home/bisheng/work/weMiniApp/Backend')

import asyncio
import os

# 使用 .env 中的数据库配置
os.environ.setdefault('APP_ENV', 'development')

from app.core.database import async_session_factory
from app.core.redis import redis_client
from app.core.security import decode_access_token, is_token_revoked
from app.models.admin_user import AdminUser
from sqlalchemy import select


async def main():
    print("=" * 60)
    print("诊断管理后台 500")

    # 1. Test Redis
    print("\n1. Redis 连通性:")
    try:
        result = await redis_client.ping()
        print(f"   ✅ ping: {result}")
    except Exception as e:
        print(f"   ❌ ping 失败: {e}")

    # 2. Test DB
    print("\n2. 数据库连通性:")
    try:
        async with async_session_factory() as db:
            result = await db.execute(select(AdminUser).where(AdminUser.username == 'admin'))
            admin = result.scalar_one_or_none()
            if admin:
                print(f"   ✅ AdminUser 查询成功: id={admin.id}, username={admin.username}, role={admin.role}")
                print(f"   is_active={admin.is_active}")
                print(f"   password_hash 长度={len(admin.password_hash)}")
            else:
                print("   ❌ admin 用户不存在")
    except Exception as e:
        print(f"   ❌ 数据库查询失败: {type(e).__name__}: {e}")

    # 3. 模拟 get_current_admin 流程
    print("\n3. 模拟 get_current_admin 流程:")
    try:
        # 先用登录获取的 token decode
        token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0eXBlIjoiYWRtaW4iLCJhZG1pbl9pZCI6MSwidXNlcm5hbWUiOiJhZG1pbiIsInJvbGUiOiJzdXBlcl9hZG1pbiIsImV4cCI6MTc4MDU2Mjg5NSwiaWF0IjoxNzgwNTU1Njk1fQ.F8dXS94r6GuKbryRAR7gsUO0El-quQGsSetQ-j2zEJA"
        payload = decode_access_token(token)
        print(f"   ✅ JWT 解析成功: type={payload.get('type')}, admin_id={payload.get('admin_id')}")

        revoked = await is_token_revoked(token)
        print(f"   ✅ is_token_revoked: {revoked}")

        async with async_session_factory() as db:
            admin_obj = await db.get(AdminUser, payload['admin_id'])
            if admin_obj:
                print(f"   ✅ db.get 成功: id={admin_obj.id}, username={admin_obj.username}, role={admin_obj.role}")
                from app.policy.permissions import ROLE_PERMISSIONS
                from app.schemas.admin import AdminInfo, AdminMeResponse
                info = AdminInfo.model_validate(admin_obj)
                print(f"   ✅ AdminInfo.model_validate 成功: {info}")
                resp = AdminMeResponse(admin=info, permissions=ROLE_PERMISSIONS.get(admin_obj.role, []))
                print(f"   ✅ AdminMeResponse 构建成功: {resp}")
            else:
                print("   ❌ db.get 返回 None")

    except Exception as e:
        import traceback
        print(f"   ❌ 模拟失败: {type(e).__name__}: {e}")
        traceback.print_exc()

    # 4. 检查 is_token_revoked 的具体行为
    print("\n4. is_token_revoked 详细测试:")
    try:
        key = "jwt:blacklist:test_key"
        await redis_client.delete(key)
        val = await redis_client.get(key)
        print(f"   ✅ GET 不存在 key: {val}")

        await redis_client.setex(key, 60, "1")
        val = await redis_client.get(key)
        print(f"   ✅ GET 存在 key: {val}")
        await redis_client.delete(key)
    except Exception as e:
        print(f"   ❌ Redis GET/SETEX 失败: {type(e).__name__}: {e}")

    print("\n" + "=" * 60)
    await redis_client.close()


if __name__ == "__main__":
    asyncio.run(main())
