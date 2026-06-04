import asyncio
import base64
import json

import httpx
from Crypto.Cipher import AES

from app.port.config import settings
from app.port.exceptions import ThirdPartyException
from app.adapter.redis import redis_get_safe, redis_setex_safe, redis_client

WECHAT_CODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"
WECHAT_TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"
ACCESS_TOKEN_CACHE_KEY = "wechat:access_token"
ACCESS_TOKEN_LOCK_KEY = "wechat:access_token:lock"


class WechatClient:
    def __init__(self):
        self.appid = settings.WECHAT_APPID
        self.secret = settings.WECHAT_SECRET

    async def code2session(self, code: str) -> dict:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0),
        ) as client:
            resp = await client.get(
                WECHAT_CODE2SESSION_URL,
                params={
                    "appid": self.appid,
                    "secret": self.secret,
                    "js_code": code,
                    "grant_type": "authorization_code",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if "errcode" in data and data["errcode"] != 0:
                raise ThirdPartyException(f"微信 code2session 失败: {data.get('errmsg', 'unknown')}")
            return data

    async def get_access_token(self) -> str:
        cached = await redis_get_safe(ACCESS_TOKEN_CACHE_KEY)
        if cached:
            return cached
        # 获取分布式锁（Redis 不可用时跳过锁，直接请求微信 API）
        try:
            acquired = await redis_client.set(ACCESS_TOKEN_LOCK_KEY, "1", nx=True, ex=10)
        except Exception:
            acquired = False
        if not acquired:
            for _ in range(30):
                await asyncio.sleep(0.1)
                cached = await redis_get_safe(ACCESS_TOKEN_CACHE_KEY)
                if cached:
                    return cached
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0),
            ) as client:
                resp = await client.get(
                    WECHAT_TOKEN_URL,
                    params={
                        "appid": self.appid,
                        "secret": self.secret,
                        "grant_type": "client_credential",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                if "errcode" in data and data["errcode"] != 0:
                    raise Exception(f"微信获取 access_token 失败: {data.get('errmsg')}")
                token = data["access_token"]
                expires_in = data.get("expires_in", 7200) - 300
                await redis_setex_safe(ACCESS_TOKEN_CACHE_KEY, expires_in, token)
                return token
        finally:
            try:
                await redis_client.delete(ACCESS_TOKEN_LOCK_KEY)
            except Exception:
                pass

    @staticmethod
    def decrypt_phone(encrypted_data: str, iv: str, session_key: str) -> str:
        session_key = base64.b64decode(session_key)
        encrypted_data = base64.b64decode(encrypted_data)
        iv = base64.b64decode(iv)
        cipher = AES.new(session_key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(encrypted_data)
        pad = decrypted[-1]
        decrypted = decrypted[:-pad]
        data = json.loads(decrypted)
        return data.get("phoneNumber", "")
