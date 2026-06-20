#!/usr/bin/env python3
"""审核系统 冒烟测试

用法:
  ADMIN_TOKEN=<admin_token> .venv/bin/python3 tests/smoke/test_review_smoke.py

前置条件: 服务运行在 http://127.0.0.1:8000
"""

import os
import sys
import json
import httpx

BASE = "http://127.0.0.1:8000"
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

pass_count = 0
fail_count = 0


def _h() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}", "Content-Type": "application/json"}


def expect(step: str, resp: httpx.Response, status: int = 200,
           checks: list[tuple[str, str]] | None = None):
    global pass_count, fail_count
    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    print(f"\n{'─' * 60}")
    print(f"  {step}")
    print(f"  status: {resp.status_code} (expected {status})")

    ok = resp.status_code == status
    if checks and ok:
        for path, expected in checks:
            val = body
            for key in path.split("."):
                if val is None:
                    break
                val = val.get(key) if isinstance(val, dict) else (
                    val[int(key)] if key.isdigit() and isinstance(val, list) else None
                )
            if str(val) != expected:
                print(f"  ❌ {path}: got={val}, expected={expected}")
                ok = False

    if ok:
        pass_count += 1
        print(f"  ✅ PASS")
    else:
        fail_count += 1
        print(f"  ❌ FAIL")
        try:
            print(f"  body: {json.dumps(body, ensure_ascii=False, indent=2)[:800]}")
        except Exception:
            print(f"  body: {resp.text[:800]}")


def main():
    global pass_count, fail_count

    if not ADMIN_TOKEN:
        print("❌ 请设置 ADMIN_TOKEN 环境变量")
        sys.exit(1)

    # ═══════════════════════════════════════════════════════════════
    # 第一阶段: 查询审核记录（空列表）
    # ═══════════════════════════════════════════════════════════════

    print("\n" + "=" * 60)
    print("  第一阶段: 查询审核记录")
    print("=" * 60)

    expect("1.1 无筛选查询审核记录",
           httpx.get(f"{BASE}/admin/reviews", headers=_h(),
                     params={"page": 1, "page_size": 5}, timeout=15),
           checks=[("code", "0")])

    # ═══════════════════════════════════════════════════════════════
    # 第二阶段: 实名认证审核
    # ═══════════════════════════════════════════════════════════════

    print("\n" + "=" * 60)
    print("  第二阶段: 实名认证审核")
    print("=" * 60)

    expect("2.1 审核不存在用户（预期 404）",
           httpx.post(f"{BASE}/admin/reviews", json={
               "target_type": "identity",
               "target_id": 999999,
               "action": "approve",
           }, headers=_h(), timeout=15),
           status=404)

    # 找有实名记录的用户: 逐个试
    resp_users = httpx.get(f"{BASE}/admin/users", headers=_h(),
                           params={"page": 1, "page_size": 10}, timeout=15)
    identity_user_id = None
    if resp_users.status_code == 200:
        for u in resp_users.json().get("data", {}).get("items", []):
            uid = u["id"]
            r = httpx.post(f"{BASE}/admin/reviews", json={
                "target_type": "identity", "target_id": uid, "action": "approve",
            }, headers=_h(), timeout=10)
            if r.status_code == 200:
                identity_user_id = uid
                break

    if identity_user_id:
        print(f"  找到实名用户 id={identity_user_id}")
        expect("2.2 审核通过实名认证",
               httpx.post(f"{BASE}/admin/reviews", json={
                   "target_type": "identity",
                   "target_id": identity_user_id,
                   "action": "approve",
               }, headers=_h(), timeout=15),
               checks=[("code", "0"), ("data.target_type", "identity"), ("data.action", "approve")])
    else:
        print(f"  ⚠️ 未找到有实名记录的用户，跳过 2.2")

    # ═══════════════════════════════════════════════════════════════
    # 第三阶段: 订单审核
    # ═══════════════════════════════════════════════════════════════

    print("\n" + "=" * 60)
    print("  第三阶段: 订单审核")
    print("=" * 60)

    expect("3.1 审核不存在订单（预期 404）",
           httpx.post(f"{BASE}/admin/reviews", json={
               "target_type": "order",
               "target_id": 999999,
               "action": "approve",
           }, headers=_h(), timeout=15),
           status=404)

    # 找一个 paid 状态的订单来审核
    resp_orders = httpx.get(f"{BASE}/admin/orders",
                            headers=_h(),
                            params={"status": "paid", "page_size": 1}, timeout=15)
    paid_order_id = None
    if resp_orders.status_code == 200:
        items = resp_orders.json().get("data", {}).get("items", [])
        if items:
            paid_order_id = items[0]["id"]
            print(f"  找到 paid 订单 id={paid_order_id}")

    if paid_order_id:
        expect("3.2 审核通过订单",
               httpx.post(f"{BASE}/admin/reviews", json={
                   "target_type": "order",
                   "target_id": paid_order_id,
                   "action": "approve",
               }, headers=_h(), timeout=15),
               checks=[("code", "0"), ("data.target_type", "order"), ("data.action", "approve")])

    # ═══════════════════════════════════════════════════════════════
    # 第四阶段: 按 target 查审核记录
    # ═══════════════════════════════════════════════════════════════

    print("\n" + "=" * 60)
    print("  第四阶段: 按审核对象查询")
    print("=" * 60)

    expect("4.1 按 target_type 查询",
           httpx.get(f"{BASE}/admin/reviews", headers=_h(),
                     params={"target_type": "identity", "page": 1, "page_size": 5}, timeout=15),
           checks=[("code", "0")])

    expect("4.2 按 target_id 查询",
           httpx.get(f"{BASE}/admin/reviews", headers=_h(),
                     params={"target_id": 338, "page": 1, "page_size": 5}, timeout=15),
           checks=[("code", "0")])

    # ═══════════════════════════════════════════════════════════════
    # 结果
    # ═══════════════════════════════════════════════════════════════

    total = pass_count + fail_count
    print(f"\n{'=' * 60}")
    print(f"  总计: {total} 项")
    print(f"  通过: {pass_count} ✅")
    print(f"  失败: {fail_count} ❌")
    print(f"{'=' * 60}")

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
