"""写入审核测试数据。

必须显式提供 ``ADMIN_SEED_USERNAME`` 和 ``ADMIN_SEED_PASSWORD``，脚本不会
创建管理员，也不包含默认凭据。
"""

import os, sys, httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

BASE = os.getenv("ADMIN_SEED_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value

# 获取 admin token
resp = httpx.post(f"{BASE}/admin/auth/login", json={
    "username": _required_env("ADMIN_SEED_USERNAME"),
    "password": _required_env("ADMIN_SEED_PASSWORD"),
}, timeout=15)
resp.raise_for_status()
token = resp.json()["data"]["access_token"]
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

reviewed = []

# 订单审核 — 通过
print("审核订单 #170 (approve)...")
r = httpx.post(f"{BASE}/admin/reviews", json={
    "target_type": "order", "target_id": 170, "action": "approve",
}, headers=headers, timeout=15)
if r.status_code == 200:
    reviewed.append(r.json()["data"])
    print(f"  ✅ review_id={reviewed[-1]['id']}")
else:
    print(f"  ⚠️ {r.json().get('message')}")

# 订单审核 — 驳回（需要另一笔 paid 订单）
resp_orders = httpx.get(f"{BASE}/admin/orders", headers=headers,
    params={"status": "paid", "page_size": 3}, timeout=15)
orders = resp_orders.json().get("data", {}).get("items", [])
for o in orders:
    if o["id"] not in {r["target_id"] for r in reviewed if r["target_type"] == "order"}:
        print(f"审核订单 #{o['id']} (reject)...")
        r2 = httpx.post(f"{BASE}/admin/reviews", json={
            "target_type": "order", "target_id": o["id"],
            "action": "reject", "comment": "考生信息与实名不匹配",
        }, headers=headers, timeout=15)
        if r2.status_code == 200:
            reviewed.append(r2.json()["data"])
            print(f"  ✅ review_id={reviewed[-1]['id']}")
        else:
            print(f"  ⚠️ {r2.json().get('message')}")
        break

# 查审核记录
print(f"\n审核记录总数:")
r = httpx.get(f"{BASE}/admin/reviews?page=1&page_size=20", headers=headers, timeout=15)
data = r.json()["data"]
print(f"  total={data['total']}, items={len(data['items'])}")
for item in data["items"][:5]:
    print(f"  #{item['id']} {item['target_type']}:{item['target_id']} {item['action']} by admin#{item['reviewer_id']} at {item['created_at']}")
