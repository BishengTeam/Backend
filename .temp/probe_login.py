"""爬取 gl.ostakp.com — 探测 API 基路径"""
import asyncio, json, re, httpx

HOST = "http://gl.ostakp.com"
USER = "17748493418"
PASS = "uu7afolo"


async def probe():
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        # 1. 先抓 HTML 看看 api base path
        resp = await client.get(HOST)
        html = resp.text
        print("=== HTML (first 3000 chars) ===")
        print(html[:3000])
        print()

        # 2. 从 HTML 中抽取 JS 文件 URL
        js_urls = re.findall(r'src="([^"]+\.js[^"]*)"', html)
        print("=== JS files ===")
        for u in js_urls[:10]:
            print(u)
        print()

        # 3. 尝试直接请求后端（不在 /api 下）
        # 已知后端是 Spring Boot，尝试 prod-api 的其他路径
        spring_endpoints = [
            "/prod-api/login",
            "/prod-api/auth/login",
            "/prod-api/system/login",
            "/prod-api/admin/login",
        ]
        for ep in spring_endpoints:
            try:
                resp = await client.post(
                    f"{HOST}{ep}",
                    json={"username": USER, "password": PASS},
                )
                print(f"[{resp.status_code}] POST {ep} → {resp.text[:200]}")
            except Exception as e:
                print(f"[ERR] POST {ep} → {e}")

        # 4. 尝试 /prod-api/captcha 获取验证码
        try:
            resp = await client.get(f"{HOST}/prod-api/captchaImage")
            print(f"\n[CAPTCHA] GET /prod-api/captchaImage → {resp.status_code} {resp.headers.get('content-type')}")
        except Exception as e:
            print(f"[CAPTCHA ERR] → {e}")


asyncio.run(probe())
