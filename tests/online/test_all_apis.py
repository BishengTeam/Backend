#!/usr/bin/env python3
"""在线接口冒烟测试 — 批量遍历所有注册路由并报告结果。

用法:
    cd /home/bisheng/work/weMiniApp/Backend && \
    python tests/online/test_all_apis.py
"""

import subprocess
import sys
import json
import time
import os

BASE_URL = "http://127.0.0.1:8000"
TIMEOUT = 10
RESULTS = []
ADMIN_USERNAME = os.getenv("ONLINE_ADMIN_USERNAME", "").strip()
ADMIN_PASSWORD = os.getenv("ONLINE_ADMIN_PASSWORD", "").strip()


def curl(method: str, path: str, *,
         headers: dict | None = None,
         body: dict | None = None,
         timeout: int = TIMEOUT) -> tuple[int, dict | None, str]:
    """Run curl and return (http_code, json_body, error_reason)."""
    cmd = ["curl", "-s", "-w", "\n%{http_code}", f"--max-time", str(timeout),
           "-X", method, f"{BASE_URL}{path}"]
    if headers:
        for k, v in headers.items():
            cmd += ["-H", f"{k}: {v}"]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]

    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=timeout + 2)
        out = out.decode("utf-8", errors="replace")
        lines = out.rsplit("\n", 1)
        http_code = int(lines[-1].strip())
        resp_text = lines[0] if len(lines) > 1 else ""
        try:
            resp_json = json.loads(resp_text) if resp_text else None
        except json.JSONDecodeError:
            resp_json = None
        return http_code, resp_json, resp_text[:200]
    except subprocess.TimeoutExpired:
        return -1, None, "timeout"
    except Exception as e:
        return -1, None, str(e)[:200]


def get_admin_token() -> str | None:
    """Use explicitly supplied administrator credentials."""
    if not ADMIN_USERNAME or not ADMIN_PASSWORD:
        print("  ⚠ 未设置 ONLINE_ADMIN_USERNAME/ONLINE_ADMIN_PASSWORD，跳过管理员接口")
        return None
    code, body, _ = curl("POST", "/admin/auth/login",
                         body={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
    if code == 200 and body and body.get("data"):
        return body["data"].get("access_token")
    print(f"  ⚠ 管理员登录失败 HTTP {code}: {body}")
    return None


def get_user_token() -> str | None:
    """Try admin login to get a valid token for auth-guarded user endpoints.
    由于微信 code2session 依赖外部服务，使用 dev_login.py 生成 token。
    """
    # Try dev_login.py
    try:
        out = subprocess.check_output(
            [sys.executable, "scripts/dev_login.py", "test_openid_user_001"],
            stderr=subprocess.STDOUT, timeout=10, cwd="/home/bisheng/work/weMiniApp/Backend"
        ).decode("utf-8")
        for line in out.split("\n"):
            if line.startswith("Token:") or "eyJ" in line:
                token = line.strip()
                if token.startswith("Token:"):
                    token = token.split("Token:")[-1].strip()
                if token and "." in token:
                    return token
    except Exception as e:
        print(f"  ⚠ dev_login.py 失败: {e}")
    return None


def test(label: str, method: str, path: str, *,
         auth: str | None = None,
         body: dict | None = None,
         auth_type: str = "public",
         required: bool = False,
         min_code: int = 200, max_code: int = 299):
    """Test one endpoint and record result."""
    headers = {}
    if auth:
        headers["Authorization"] = f"Bearer {auth}"

    code, resp, raw = curl(method, path, headers=headers, body=body)

    ok = min_code <= code <= max_code if code > 0 else False
    status = "✅" if ok else "❌"
    detail = ""
    if resp and "message" in resp:
        detail = resp.get("message", "")
    if not ok:
        detail = f"{detail} ({raw})" if raw else str(code)

    RESULTS.append({
        "label": label,
        "method": method,
        "path": path,
        "auth_type": auth_type,
        "http_code": code,
        "ok": ok,
        "detail": detail,
    })
    print(f"{status} {method:<7} {path:<55}  HTTP {code}  {detail[:60]}")


def main():
    print("=" * 80)
    print("🔍 后端接口在线冒烟测试")
    print(f"   目标: {BASE_URL}")
    print("=" * 80)

    # Get tokens
    print("\n--- 获取认证 Token ---")
    admin_token = get_admin_token()
    user_token = get_user_token()
    print(f"  管理员 Token: {'✅' if admin_token else '❌'}")
    print(f"  用户 Token:   {'✅' if user_token else '❌'}")

    # ================================================================
    # 一、公开接口（无需认证）
    # ================================================================
    print("\n--- 1. 公开接口（无需认证）---")
    test("健康检查", "GET", "/health")
    test("就绪探针", "GET", "/ready")

    # ================================================================
    # 二、用户认证
    # ================================================================
    print("\n--- 2. 认证模块 ---")
    # login 需要微信code，预期会返回非200（但服务应正常响应）
    test("微信登录（无code）", "POST", "/api/auth/login", body={"code": ""})

    # 需要有效 token 的接口
    if user_token:
        test("刷新token（无效token）", "POST", "/api/auth/refresh",
             auth=user_token, body={"refresh_token": "invalid"}, auth_type="user")
        test("退出登录", "POST", "/api/auth/logout",
             auth=user_token, body={"refresh_token": "invalid"}, auth_type="user")

    # ================================================================
    # 三、公开数据接口
    # ================================================================
    print("\n--- 3. 公开数据接口 ---")
    test("认证列表", "GET", "/api/cert/certifications")
    test("认证标签", "GET", "/api/cert/certifications/tags")
    test("课程列表", "GET", "/api/courses")
    test("课程类目", "GET", "/api/courses/categories")
    test("价格列表", "GET", "/api/prices")
    test("题库分类", "GET", "/api/quiz/categories")
    test("题目列表", "GET", "/api/quiz/questions")
    test("快速问题", "GET", "/api/quick-questions")
    test("竞赛统计", "GET", "/api/competition/stats")
    test("专区首页", "GET", "/api/zones")
    test("认证专区", "GET", "/api/zones/cert")
    test("学习专区", "GET", "/api/zones/study")
    test("活动专区", "GET", "/api/zones/activity")
    test("就业专区", "GET", "/api/zones/employment")
    test("系统海报", "GET", "/api/system/poster")
    test("活动列表", "GET", "/api/activities")
    test("就业岗位列表", "GET", "/api/jobs")

    # ================================================================
    # 四、用户需认证接口
    # ================================================================
    print("\n--- 4. 用户需认证接口 ---")
    if user_token:
        test("用户个人信息", "GET", "/api/user/profile", auth=user_token, auth_type="user")
        test("查询实名状态", "GET", "/api/user/identity", auth=user_token, auth_type="user")
        test("我的课程", "GET", "/api/courses/my", auth=user_token, auth_type="user")
        test("积分余额", "GET", "/api/points", auth=user_token, auth_type="user")
        test("积分记录", "GET", "/api/points/history", auth=user_token, auth_type="user")
        test("收藏列表", "GET", "/api/collections", auth=user_token, auth_type="user")
        test("优惠券列表", "GET", "/api/coupons", auth=user_token, auth_type="user")
        test("协议列表", "GET", "/api/agreements", auth=user_token, auth_type="user")
        test("用户订单列表", "GET", "/api/orders", auth=user_token, auth_type="user")
        test("用户工单列表", "GET", "/api/tickets", auth=user_token, auth_type="user")
        test("竞赛赛道", "GET", "/api/competition/tracks", auth=user_token, auth_type="user")
        test("竞赛导出", "GET", "/api/competition/export", auth=user_token, auth_type="user")
        test("活动导出", "GET", "/api/activities/export", auth=user_token, auth_type="user")
        test("错题本", "GET", "/api/quiz/wrong-book", auth=user_token, auth_type="user")
        test("收藏题库", "GET", "/api/quiz/collections", auth=user_token, auth_type="user")
        test("打卡状态", "GET", "/api/quiz/checkin", auth=user_token, auth_type="user")
        test("客服消息（无消息）", "POST", "/api/chat",
             auth=user_token, body={"content": ""}, auth_type="user")
        test("客服流式（无消息）", "GET", "/api/chat/stream", auth=user_token, auth_type="user")

        # 需要 body 的 POST
        test("提交实名认证（无效数据）", "POST", "/api/user/identity",
             auth=user_token, body={"real_name": "", "id_number": ""}, auth_type="user")
        test("手机解密（无效数据）", "POST", "/api/user/phone/decrypt",
             auth=user_token, body={"encrypted_data": "", "iv": ""}, auth_type="user")
        test("提交答题（无数据）", "POST", "/api/quiz/submit",
             auth=user_token, body={}, auth_type="user")
        test("打卡签到", "POST", "/api/quiz/checkin",
             auth=user_token, body={}, auth_type="user")
        test("分享链接生成", "POST", "/api/share",
             auth=user_token, body={"target_type": "cert", "target_id": 1}, auth_type="user")
        test("创建工单（无效数据）", "POST", "/api/tickets",
             auth=user_token, body={"type": "", "description": ""}, auth_type="user")
    else:
        for path in [
            "/api/user/profile", "/api/courses/my", "/api/points",
            "/api/collections", "/api/coupons", "/api/agreements",
            "/api/orders", "/api/tickets", "/api/quiz/wrong-book",
            "/api/quiz/collections", "/api/quiz/checkin",
        ]:
            test(f"需认证-{path.split('/')[-1]}", "GET", path, auth_type="user")

    # ================================================================
    # 五、管理后台认证
    # ================================================================
    print("\n--- 5. 管理后台认证 ---")
    if ADMIN_USERNAME and ADMIN_PASSWORD:
        test("管理员登录", "POST", "/admin/auth/login",
             body={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
        test("管理员登录（错误密码）", "POST", "/admin/auth/login",
             body={"username": ADMIN_USERNAME, "password": f"{ADMIN_PASSWORD}-invalid"})
    if admin_token:
        test("管理员信息", "GET", "/admin/auth/me", auth=admin_token, auth_type="admin")

    # ================================================================
    # 六、管理后台数据接口
    # ================================================================
    print("\n--- 6. 管理后台接口 ---")
    if admin_token:
        # 用户管理
        test("管理员-用户列表", "GET", "/admin/users", auth=admin_token, auth_type="admin")
        test("管理员-用户导出", "GET", "/admin/users/export", auth=admin_token, auth_type="admin")
        test("管理员-订单列表", "GET", "/admin/orders", auth=admin_token, auth_type="admin")
        test("管理员-订单导出", "GET", "/admin/orders/export", auth=admin_token, auth_type="admin")
        test("管理员-对账", "GET", "/admin/orders/reconciliation", auth=admin_token, auth_type="admin")
        test("管理员-课程列表", "GET", "/admin/courses", auth=admin_token, auth_type="admin")
        test("管理员-优惠券库", "GET", "/admin/coupons", auth=admin_token, auth_type="admin")
        test("管理员-协议列表", "GET", "/admin/agreements", auth=admin_token, auth_type="admin")
        test("管理员-工单列表", "GET", "/admin/tickets", auth=admin_token, auth_type="admin")
        test("管理员-数据看板", "GET", "/admin/statistics/dashboard", auth=admin_token, auth_type="admin")
        test("管理员-系统设置-管理员列表", "GET", "/admin/settings/admins", auth=admin_token, auth_type="admin")
        test("管理员-专区列表", "GET", "/admin/zones", auth=admin_token, auth_type="admin")
        test("管理员-Banner列表", "GET", "/admin/banners", auth=admin_token, auth_type="admin")
        test("管理员-题库分类", "GET", "/admin/quiz/categories", auth=admin_token, auth_type="admin")
        test("管理员-题库列表", "GET", "/admin/quiz/questions", auth=admin_token, auth_type="admin")
        test("管理员-竞赛导出", "GET", "/admin/competition/export", auth=admin_token, auth_type="admin")
    else:
        print("  ⚠ 无管理员 token，跳过管理后台接口测试")

    # ================================================================
    # 七、支付回调（无需用户认证，但需要有效签名）
    # ================================================================
    print("\n--- 7. 支付回调 ---")
    test("支付回调（无签名）", "POST", "/api/payment/callback", body={"id": "fake"})

    # ================================================================
    # 八、分享公开
    # ================================================================
    print("\n--- 8. 分享公开 ---")
    test("分享追踪（无效code）", "GET", "/api/share/notexist")

    # ================================================================
    # 九、文件上传/访问
    # ================================================================
    print("\n--- 9. 文件上传/访问 ---")
    if user_token:
        test("文件上传（无文件）", "POST", "/api/upload", auth=user_token, auth_type="user")
    test("文件访问（无效id）", "GET", "/api/media/99999")

    # ================================================================
    # 汇总
    # ================================================================
    print("\n" + "=" * 80)
    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r["ok"])
    failed = total - passed

    print(f"\n📊 汇总: {passed}/{total} 通过")

    if failed:
        print(f"\n❌ 失败 ({failed}):")
        for r in RESULTS:
            if not r["ok"]:
                print(f"   {r['method']:<7} {r['label']:<40}  HTTP {r['http_code']}  {r['detail']}")

    # 按 HTTP code 分类
    codes = {}
    for r in RESULTS:
        c = r["http_code"]
        codes[c] = codes.get(c, 0) + 1
    print("\n📊 HTTP 状态码分布:")
    for c, n in sorted(codes.items()):
        print(f"   HTTP {c}: {n}")

    # 保存 JSON 报告
    report = {
        "base_url": BASE_URL,
        "total": total,
        "passed": passed,
        "failed": failed,
        "results": RESULTS,
    }
    report_path = "/tmp/api_test_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n📄 详细报告保存至: {report_path}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
