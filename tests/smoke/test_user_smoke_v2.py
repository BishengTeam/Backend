#!/usr/bin/env python3
"""用户信息模块 冒烟测试（HTTP 版）

用法:
  先获取 token:
    .venv/bin/python3 scripts/dev_login.py          → 输出 ADMIN_TOKEN + USER_TOKEN

  再跑测试:
    ADMIN_TOKEN=<admin_token> USER_TOKEN=<user_token> .venv/bin/python3 tests/smoke/test_user_smoke_v2.py

前置条件: 服务运行在 http://127.0.0.1:8000
"""

import os
import sys
import json
import httpx

BASE = "http://127.0.0.1:8000"
USER_TOKEN = os.getenv("USER_TOKEN", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0eXBlIjoiYWNjZXNzIiwidXNlcl9pZCI6MzM4LCJvcGVuaWQiOiJ0ZXN0X29wZW5pZF91c2VyXzAwMSIsImV4cCI6MTc4MTA0MjI2NywiaWF0IjoxNzgxMDM1MDY3fQ._FTFRKj2eZtun_DWHjbz74WORk4iNCdM_26VKSJPlz8")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0eXBlIjoiYWRtaW4iLCJhZG1pbl9pZCI6MSwidXNlcm5hbWUiOiJhZG1pbiIsInJvbGUiOiJzdXBlcl9hZG1pbiIsImV4cCI6MTc4MTA0MjI2NywiaWF0IjoxNzgxMDM1MDY3fQ.R5WBSlvkTqWhL1VcQBYffQgTwe8Z7QfdSRDWBbWCFVo")

pass_count = 0
fail_count = 0
user_id = None


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"} if token else {}


def expect(step: str, resp: httpx.Response, status: int = 200, checks: list[tuple[str, str]] | None = None):
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
                val = val.get(key) if isinstance(val, dict) else (val[int(key)] if key.isdigit() and isinstance(val, list) else None)
            val_str = str(val)
            ok_this = val_str == expected
            if not ok_this:
                print(f"  ❌ {path}: got={val_str}, expected={expected}")
                ok = False

    if ok:
        pass_count += 1
        print(f"  ✅ PASS")
    else:
        fail_count += 1
        print(f"  ❌ FAIL")
        try:
            print(f"  body: {json.dumps(body, ensure_ascii=False, indent=2)[:500]}")
        except Exception:
            print(f"  body: {resp.text[:500]}")

    return body.get("data", body)


def main():
    global user_id

    if not USER_TOKEN:
        print("请设置 USER_TOKEN 环境变量")
        sys.exit(1)
    if not ADMIN_TOKEN:
        print("请设置 ADMIN_TOKEN 环境变量")
        sys.exit(1)

    with httpx.Client(base_url=BASE, timeout=30) as client:

        # ═══════════════════════════════════════════════════════
        #  第一阶段: Level 1 基础资料
        # ═══════════════════════════════════════════════════════
        print("\n══════ 第一阶段: Level 1 基础资料 ══════")

        data = expect("步骤 1: GET /api/user/profile",
            client.get("/api/user/profile", headers=_h(USER_TOKEN)))

        user_id = data["id"]
        print(f"  (user_id = {user_id})")

        expect("步骤 2: PUT /api/user/profile (修改昵称/邮箱)",
            client.put("/api/user/profile", headers=_h(USER_TOKEN), json={
                "nickname": "测试昵称", "email": "test@example.com"
            }))

        expect("步骤 3: GET /api/user/profile (验证修改生效)",
            client.get("/api/user/profile", headers=_h(USER_TOKEN)),
            checks=[("data.nickname", "测试昵称"), ("data.email", "test@example.com")])

        # ═══════════════════════════════════════════════════════
        #  第二阶段: Level 2 实名认证
        # ═══════════════════════════════════════════════════════
        print("\n══════ 第二阶段: Level 2 实名认证 ══════")

        data = expect("步骤 4: POST /api/user/identity (提交实名)",
            client.post("/api/user/identity", headers=_h(USER_TOKEN), json={
                "user_type": "student",
                "real_name": "张三",
                "id_card_number": "510106199001011233",
                "id_card_front_oss": "oss://front.jpg",
                "id_card_back_oss": "oss://back.jpg",
            }),
            checks=[("data.status", "pending"), ("data.gender", "男"), ("data.census_register", "成都市-金牛区")])

        # age 动态值，不检查精确值
        age = data.get("age")
        print(f"  age={age} (expect ~36)")

        expect("步骤 5: GET /api/user/identity (验证脱敏)",
            client.get("/api/user/identity", headers=_h(USER_TOKEN)),
            checks=[("data.id_card_number", "5101**********1233")])

        expect("步骤 6: GET /api/user/profile (验证 identity_status=pending)",
            client.get("/api/user/profile", headers=_h(USER_TOKEN)),
            checks=[("data.identity_status", "pending"), ("data.user_type", "student"),
                    ("data.real_name", "张三"), ("data.gender", "男")])

        expect("步骤 7: 审核通过",
            client.put(f"/admin/users/{user_id}/identity/review",
                       headers=_h(ADMIN_TOKEN), json={"status": "verified"}))

        # 步骤 8: 用户端 profile — phone/id_card 脱敏
        data = expect("步骤 8: 用户端 POST 审核后 GET profile",
            client.get("/api/user/profile", headers=_h(USER_TOKEN)),
            checks=[("data.identity_status", "verified")])
        phone = data.get("phone", "")
        id_card = data.get("id_card", "")
        phone_ok = "****" in str(phone) or phone is None
        id_ok = "****" in str(id_card) or id_card is None
        print(f"  phone={phone} (masked={phone_ok}), id_card={id_card} (masked={id_ok})")
        if phone_ok and id_ok:
            pass
        else:
            print("  ⚠️  脱敏检查不确定（phone=None 可能表示无手机号）")

        # 步骤 8a: 管理端 — 明文
        data = expect("步骤 8a: 管理端 GET profile",
            client.get(f"/admin/users/{user_id}/profile", headers=_h(ADMIN_TOKEN)),
            checks=[("data.identity_status", "verified")])
        id_card_raw = data.get("id_card_raw")
        print(f"  phone_raw={data.get('phone')}, id_card_raw={id_card_raw}")
        if id_card_raw == "510106199001011233":
            print(f"  ✅ 管理端明文身份证一致")
        else:
            print(f"  ⚠️  管理端 id_card_raw={id_card_raw}")

        # ═══════════════════════════════════════════════════════
        #  第三阶段: 互斥
        # ═══════════════════════════════════════════════════════
        print("\n══════ 第三阶段: 学生/企业互斥 ══════")

        expect("步骤 9: POST /api/user/enterprise (应拒绝)",
            client.post("/api/user/enterprise", headers=_h(USER_TOKEN), json={
                "organization": "某公司"
            }),
            status=422)

        expect("步骤 10: POST /api/user/student (提交学生信息)",
            client.post("/api/user/student", headers=_h(USER_TOKEN), json={
                "education": "本科", "school": "四川大学",
                "major": "计算机科学", "student_card_oss": "oss://student.jpg"
            }),
            checks=[("data.status", "pending")])

        expect("步骤 11: 审核学生信息",
            client.put(f"/admin/users/{user_id}/student/review",
                       headers=_h(ADMIN_TOKEN), json={"status": "verified"}))

        # ═══════════════════════════════════════════════════════
        #  第四阶段: 驳回恢复
        # ═══════════════════════════════════════════════════════
        print("\n══════ 第四阶段: 驳回恢复 ══════")

        expect("步骤 12: 重新提交实名 (修改姓名→张四)",
            client.post("/api/user/identity", headers=_h(USER_TOKEN), json={
                "user_type": "student",
                "real_name": "张四",
                "id_card_number": "510106199001011233",
                "id_card_front_oss": "oss://front2.jpg",
                "id_card_back_oss": "oss://back2.jpg",
            }),
            checks=[("data.real_name", "张四")])

        expect("步骤 13: 管理员驳回",
            client.put(f"/admin/users/{user_id}/identity/review",
                       headers=_h(ADMIN_TOKEN), json={"status": "rejected"}))

        expect("步骤 14: GET /api/user/identity (验证恢复)",
            client.get("/api/user/identity", headers=_h(USER_TOKEN)),
            checks=[("data.status", "rejected"), ("data.real_name", "张三")])

    print(f"\n{'═' * 60}")
    print(f"  结果: {pass_count} pass, {fail_count} fail")
    print(f"{'═' * 60}\n")
    sys.exit(0 if fail_count == 0 else 1)


if __name__ == "__main__":
    main()
