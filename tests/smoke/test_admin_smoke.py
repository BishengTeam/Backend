"""Admin Smoke Test — confirms all core admin endpoints respond correctly.

Usage: python3 tests/smoke/test_admin_smoke.py
Requires: httpx (pip install httpx), target service running on localhost:8000
"""

import asyncio
import sys
import time

import httpx

BASE = "http://localhost:8000"
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"

# Each entry: (method, path, check_fn or None)
# check_fn receives the response body (dict) and returns True/False
CASES = [
    # ── Auth ──
    ("POST", "/admin/auth/login", None),  # special: login then extract token
    ("GET", "/admin/auth/me", lambda d: "admin" in d and "permissions" in d),
    ("POST", "/admin/auth/logout", lambda d: True),

    # ── Users ──
    ("GET", "/admin/users?page=1&page_size=20", lambda d: isinstance(d.get("items"), list)),
    ("GET", "/admin/users?phone=13800000000", lambda d: isinstance(d.get("items"), list)),

    # ── Orders ──
    ("GET", "/admin/orders?page=1&page_size=10", lambda d: isinstance(d.get("items"), list)),

    # ── Courses ──
    ("GET", "/admin/courses", lambda d: isinstance(d.get("items"), list)),

    # ── Quiz ──
    ("GET", "/admin/quiz/categories", lambda d: isinstance(d, list)),
    ("GET", "/admin/quiz/questions?page=1&page_size=10", lambda d: isinstance(d.get("items"), list)),

    # ── Banners (returns list directly, not paginated)
    ("GET", "/admin/banners", lambda d: isinstance(d, list)),

    # ── Zones ──
    ("GET", "/admin/zones?page=1&page_size=10", lambda d: isinstance(d.get("items"), list)),

    # ── Dashboard ──
    ("GET", "/admin/statistics/dashboard", lambda d: isinstance(d, dict)),

    # ── Settings ──
    ("GET", "/admin/settings/admins", lambda d: isinstance(d.get("items"), list)),

    # ── Coupons ──
    ("GET", "/admin/coupons", lambda d: isinstance(d.get("items"), list)),
]


async def main():
    print("=== Admin Smoke Test ===")
    print(f"Target: {BASE}")
    print("=" * 40)

    passed = 0
    failed = 0
    token = None
    t0 = time.monotonic()

    async with httpx.AsyncClient(base_url=BASE, timeout=10.0) as client:
        for method, path, check in CASES:
            if path == "/admin/auth/login":
                # Login first to obtain token
                resp = await client.post(path, json={
                    "username": ADMIN_USERNAME,
                    "password": ADMIN_PASSWORD,
                })
                elapsed = resp.elapsed.total_seconds() * 1000
                body = _safe_json(resp)

                if resp.status_code == 200 and body.get("code") == 0:
                    token = body["data"]["access_token"]
                    print(f"  PASS  POST {path}  ({elapsed:.0f}ms)")
                    passed += 1
                else:
                    print(f"  FAIL  POST {path}  status={resp.status_code} code={body.get('code')} body={body}")
                    failed += 1
                continue

            # All other requests use token
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            resp = await client.request(method, path, headers=headers)
            elapsed = resp.elapsed.total_seconds() * 1000
            body = _safe_json(resp)

            ok = resp.status_code == 200 and body.get("code") == 0
            if ok and check and body.get("data") is not None:
                ok = check(body["data"])

            if ok:
                print(f"  PASS  {method:4s} {path}  ({elapsed:.0f}ms)")
                passed += 1
            else:
                print(f"  FAIL  {method:4s} {path}  status={resp.status_code} code={body.get('code')}")
                failed += 1

    total = passed + failed
    elapsed_total = time.monotonic() - t0
    print("-" * 40)
    print(f"Result: {passed}/{total} passed ({elapsed_total:.1f}s)")
    if failed:
        print("!! Some tests FAILED !!")
        sys.exit(1)


def _safe_json(resp: httpx.Response) -> dict:
    try:
        return resp.json()
    except Exception:
        return {}


if __name__ == "__main__":
    asyncio.run(main())
