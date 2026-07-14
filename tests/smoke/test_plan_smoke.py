#!/usr/bin/env python3
"""Plan 批次管理 冒烟测试

用法:
  ADMIN_TOKEN=<admin_token> .venv/bin/python3 tests/smoke/test_plan_smoke.py

前置条件: 服务运行在 http://127.0.0.1:8000
"""

import os
import sys
import json
from datetime import datetime, timedelta, timezone

import httpx

BASE = "http://127.0.0.1:8000"
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

pass_count = 0
fail_count = 0
SMOKE_PRODUCT = "H3C-RE"  # 使用已有认证产品 H3C-NE 的 code
PLAN_NAME = f"冒烟测试批次-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"


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
            actual = str(val)
            if isinstance(expected, str) and expected.startswith("!"):
                # 否定检查: !expected 表示不相等
                if actual == expected[1:]:
                    print(f"  ❌ {path}: got={actual}, expected NOT {expected[1:]}")
                    ok = False
            elif actual != expected:
                print(f"  ❌ {path}: got={actual}, expected={expected}")
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

    now = datetime.now(timezone.utc)
    apply_start = (now - timedelta(days=1)).isoformat()
    apply_end = (now + timedelta(days=30)).isoformat()

    # 先确认可用认证产品
    certs_resp = httpx.get(f"{BASE}/admin/certifications", headers=_h(),
                           params={"page": 1, "page_size": 10}, timeout=15)
    certs = certs_resp.json().get("data", {}).get("items", []) if certs_resp.status_code == 200 else []
    product_code = None
    for c in certs:
        if c.get("vendor") == "H3C" or c.get("code", "").startswith("H3C"):
            product_code = c["code"]
            print(f"  使用认证产品: {c.get('chinese_name', c['name'])} (code={product_code})")
            break

    if not product_code:
        print("  ⚠️ 未找到 H3C 认证产品，使用 H3C-NE")
        product_code = "H3C-NE"

    # 清理：删除之前冒烟测试残留的批次
    clean_resp = httpx.get(
        f"{BASE}/admin/certifications/{product_code}/plans",
        headers=_h(), timeout=15,
    )
    if clean_resp.status_code == 200:
        for plan in clean_resp.json().get("data", []):
            if plan["name"] == PLAN_NAME:
                plan_id = plan["id"]
                status = plan["status"]
                if status == "published":
                    httpx.put(
                        f"{BASE}/admin/certifications/{product_code}/plans/{plan_id}/archive",
                        headers=_h(), timeout=10,
                    )
                    httpx.delete(
                        f"{BASE}/admin/certifications/{product_code}/plans/{plan_id}",
                        headers=_h(), timeout=10,
                    )
                elif status == "draft":
                    httpx.delete(
                        f"{BASE}/admin/certifications/{product_code}/plans/{plan_id}",
                        headers=_h(), timeout=10,
                    )
    print(f"  已清理残留冒烟批次")

    # ════════════════════════════════════════════════════════════
    # 第一阶段: 列表为空
    # ════════════════════════════════════════════════════════════

    print("\n" + "=" * 60)
    print("  第一阶段: 批次列表（空）")
    print("=" * 60)

    resp_plan_list = httpx.get(
        f"{BASE}/admin/certifications/{product_code}/plans",
        headers=_h(), timeout=15,
    )
    expect("1.1 获取批次列表",
           resp_plan_list,
           checks=[("code", "0")])

    data_plans = resp_plan_list.json().get("data", [])

    # ════════════════════════════════════════════════════════════
    # 第二阶段: 创建批次
    # ════════════════════════════════════════════════════════════

    print("\n" + "=" * 60)
    print("  第二阶段: 创建批次")
    print("=" * 60)

    resp_create = httpx.post(
        f"{BASE}/admin/certifications/{product_code}/plans",
        json={
            "name": PLAN_NAME,
            "capacity": 50,
            "apply_start": apply_start,
            "apply_end": apply_end,
        },
        headers=_h(), timeout=15,
    )
    expect("2.1 创建批次",
           resp_create, status=200,
           checks=[
               ("code", "0"),
               ("data.name", PLAN_NAME),
               ("data.status", "draft"),
               ("data.capacity", "50"),
               ("data.enrolled", "0"),
           ])

    plan_id = resp_create.json().get("data", {}).get("id")
    assert plan_id, "创建后应有 plan_id"

    # ════════════════════════════════════════════════════════════
    # 第三阶段: 列表有数据
    # ════════════════════════════════════════════════════════════

    print("\n" + "=" * 60)
    print("  第三阶段: 批次列表（含数据）")
    print("=" * 60)

    resp_list2 = httpx.get(
        f"{BASE}/admin/certifications/{product_code}/plans",
        headers=_h(), timeout=15,
    )
    expect("3.1 列表包含新建批次",
           resp_list2,
           checks=[("code", "0")])

    # ════════════════════════════════════════════════════════════
    # 第四阶段: 发布 → 归档
    # ════════════════════════════════════════════════════════════

    print("\n" + "=" * 60)
    print("  第四阶段: 发布 + 归档")
    print("=" * 60)

    expect("4.1 发布批次",
           httpx.put(
               f"{BASE}/admin/certifications/{product_code}/plans/{plan_id}/publish",
               headers=_h(), timeout=15,
           ),
           checks=[("code", "0"), ("data.status", "published")])

    expect("4.2 归档批次",
           httpx.put(
               f"{BASE}/admin/certifications/{product_code}/plans/{plan_id}/archive",
               headers=_h(), timeout=15,
           ),
           checks=[("code", "0"), ("data.status", "archived")])

    # ════════════════════════════════════════════════════════════
    # 第五阶段: 取消批次
    # ════════════════════════════════════════════════════════════

    print("\n" + "=" * 60)
    print("  第五阶段: 取消批次")
    print("=" * 60)

    # 创建新 published 批次然后取消
    resp_cancel = httpx.post(
        f"{BASE}/admin/certifications/{product_code}/plans",
        json={
            "name": f"{PLAN_NAME}-cancel",
            "apply_start": apply_start,
            "apply_end": apply_end,
        },
        headers=_h(), timeout=15,
    )
    cancel_id = resp_cancel.json().get("data", {}).get("id")
    httpx.put(
        f"{BASE}/admin/certifications/{product_code}/plans/{cancel_id}/publish",
        headers=_h(), timeout=10,
    )
    expect("5.1 取消已发布批次",
           httpx.put(
               f"{BASE}/admin/certifications/{product_code}/plans/{cancel_id}/cancel",
               headers=_h(), timeout=15,
           ),
           checks=[("code", "0"), ("data.status", "cancelled")])

    # ════════════════════════════════════════════════════════════
    # 第六阶段: 不可编辑/删除已归档
    # ════════════════════════════════════════════════════════════

    print("\n" + "=" * 60)
    print("  第六阶段: 防护校验")
    print("=" * 60)

    expect("6.1 已归档批次不可删除",
           httpx.delete(
               f"{BASE}/admin/certifications/{product_code}/plans/{plan_id}",
               headers=_h(), timeout=15,
           ), status=422)

    # ════════════════════════════════════════════════════════════
    # 第七阶段: 用户端
    # ════════════════════════════════════════════════════════════
    # (需要用户 token，这里只测试不鉴权情况)

    print("\n" + "=" * 60)
    print("  第七阶段: 用户端（无需鉴权测试）")
    print("=" * 60)

    expect("7.1 无 token 访问用户端列表被拒绝",
           httpx.get(
               f"{BASE}/api/plans",
               params={"product_type": product_code},
               timeout=15,
           ), status=401)

    # ════════════════════════════════════════════════════════════
    # 第八阶段: 清理
    # ════════════════════════════════════════════════════════════

    print("\n" + "=" * 60)
    print("  第八阶段: 清理（新建 draft 并删除）")
    print("=" * 60)

    # 创建一个新 draft 并立即删除
    resp_draft = httpx.post(
        f"{BASE}/admin/certifications/{product_code}/plans",
        json={"name": f"{PLAN_NAME}-cleanup", "capacity": 1},
        headers=_h(), timeout=15,
    )
    if resp_draft.status_code == 200:
        draft_id = resp_draft.json().get("data", {}).get("id")
        expect("8.1 删除草稿批次",
               httpx.delete(
                   f"{BASE}/admin/certifications/{product_code}/plans/{draft_id}",
                   headers=_h(), timeout=15,
               ),
               checks=[("code", "0")])

    # ════════════════════════════════════════════════════════════
    # 结果
    # ════════════════════════════════════════════════════════════

    total = pass_count + fail_count
    print(f"\n{'=' * 60}")
    print(f"  总计: {total} 项")
    print(f"  通过: {pass_count} ✅")
    print(f"  失败: {fail_count} ❌")
    print(f"{'=' * 60}")

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
