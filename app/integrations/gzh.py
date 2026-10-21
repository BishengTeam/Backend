"""公众号开放接口客户端：仅供 Backend 定时同步推文用。

AppSecret 走 GZH_APPID / GZH_APP_SECRET 配置（支持 *_FILE 间接层），
与小程序登录用的 WECHAT_APPID / WECHAT_SECRET 相互独立。
红线：密钥只存在后端，绝不进小程序端。
"""

import asyncio

import httpx

from app.adapter.redis import redis_client, redis_get_safe, redis_setex_safe
from app.port.config import settings

GZH_TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"
GZH_FREEPUBLISH_URL = "https://api.weixin.qq.com/cgi-bin/freepublish/batchget"
ACCESS_TOKEN_CACHE_KEY = "gzh:access_token"
ACCESS_TOKEN_LOCK_KEY = "gzh:access_token:lock"


def is_configured() -> bool:
    return bool(settings.GZH_APPID and settings.GZH_APP_SECRET)


class GzhClient:
    """与 WechatClient 同款：Redis 缓存 token + 分布式锁防并发刷接口。"""

    def __init__(self):
        self.appid = settings.GZH_APPID
        self.secret = settings.GZH_APP_SECRET

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
                    GZH_TOKEN_URL,
                    params={
                        "appid": self.appid,
                        "secret": self.secret,
                        "grant_type": "client_credential",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                if "errcode" in data and data["errcode"] != 0:
                    raise Exception(f"公众号获取 access_token 失败: {data.get('errmsg')}")
                token = data["access_token"]
                expires_in = data.get("expires_in", 7200) - 300
                await redis_setex_safe(ACCESS_TOKEN_CACHE_KEY, expires_in, token)
                return token
        finally:
            try:
                await redis_client.delete(ACCESS_TOKEN_LOCK_KEY)
            except Exception:
                pass

    async def fetch_published_articles(self, count: int = 20) -> dict:
        """freepublish/batchget：取成功发布的最新图文（no_content 省正文流量）。"""
        token = await self.get_access_token()
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0),
        ) as client:
            resp = await client.post(
                GZH_FREEPUBLISH_URL,
                params={"access_token": token},
                json={"offset": 0, "count": count, "no_content": 1},
            )
            resp.raise_for_status()
            data = resp.json()
            if "errcode" in data and data["errcode"] != 0:
                raise Exception(
                    f"公众号拉取发布列表失败: errcode={data.get('errcode')}, errmsg={data.get('errmsg', 'unknown')}"
                )
            return data
