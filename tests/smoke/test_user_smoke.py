"""
用户模块冒烟测试 — 全字段覆盖

用法:
    TOKEN=<token> .venv/bin/python tests/smoke/test_user_smoke.py
"""
import os
import httpx

BASE = "http://127.0.0.1:8000"
TOKEN = os.environ.get("TOKEN", "")
if not TOKEN:
    token_file = os.path.join(os.path.dirname(__file__), '..', '..', '.temp', 'token.txt')
    if os.path.exists(token_file):
        with open(token_file) as f: TOKEN = f.read().strip()
if not TOKEN:
    print("❌ 请设置 TOKEN 环境变量"); exit(1)

ok = fail = 0
h = {"Authorization": f"Bearer {TOKEN}"}

def check(name, resp, extra=None):
    global ok, fail
    if resp.status_code == 200 and (extra is None or extra(resp)):
        ok += 1; print(f"  ✅ {name}")
    else:
        fail += 1
        detail = f" body={resp.text[:120]}" if resp.status_code != 200 else ""
        print(f"  ❌ {name} — status={resp.status_code}{detail}")

with httpx.Client(base_url=BASE, timeout=30) as c:
    print("🧪 用户模块冒烟测试")

    # 1. 获取当前个人信息
    r = c.get("/api/user/profile", headers=h)
    check("1. 获取个人信息", r, lambda r: r.json()["data"]["id"] > 0)
    p = r.json().get("data", {}) if r.status_code == 200 else {}
    print(f"     phone={p.get('phone')} email={p.get('email')} real_name={p.get('real_name')}")

    # 2. 编辑所有可填字段
    r = c.put("/api/user/profile", json={
        "phone":       "13900000001",
        "email":       "test@example.com",
        "gender":      "男",
        "education":   "本科",
        "school":      "测试大学",
        "major":       "计算机科学",
        "organization":"测试单位",
    }, headers=h)
    check("2. 编辑个人信息", r)
    p2 = r.json().get("data", {}) if r.status_code == 200 else {}

    # 3. 验证编辑结果
    assert p2.get("phone") == "139****0001", f"phone 脱敏错误: {p2.get('phone')}"
    assert p2.get("email") == "test@example.com", f"email: {p2.get('email')}"
    assert p2.get("gender") == "男", f"gender: {p2.get('gender')}"
    assert p2.get("education") == "本科", f"education: {p2.get('education')}"
    assert p2.get("school") == "测试大学", f"school: {p2.get('school')}"
    assert p2.get("major") == "计算机科学", f"major: {p2.get('major')}"
    assert p2.get("organization") == "测试单位", f"organization: {p2.get('organization')}"
    check("3. 验证编辑结果", r)  # all asserts passed, just mark ok

    # 4. 提交实名认证
    r = c.post("/api/user/identity", json={
        "user_type": "enterprise",
        "real_name": "测试用户",
        "id_card_number": "110101199001011237",
    }, headers=h)
    check("4. 提交实名认证", r)
    status = r.json().get("data", {}).get("status", "") if r.status_code == 200 else ""

    # 5. 查询实名认证（脱敏）
    r = c.get("/api/user/identity", headers=h)
    check("5. 查询实名认证", r, lambda r: "**********" in r.json().get("data", {}).get("id_card_number", ""))

    # 6. 获取个人信息 — 验证衍生字段
    r = c.get("/api/user/profile", headers=h)
    check("6. 衍生字段", r)
    p6 = r.json().get("data", {}) if r.status_code == 200 else {}
    print(f"     age={p6.get('age')} pinyin={p6.get('pinyin')} first_name={p6.get('first_name')} last_name={p6.get('last_name')}")
    # pending 状态下 id_card_raw / phone_raw 应为 null
    assert p6.get("id_card_raw") is None, f"id_card_raw pending 时应为 null: {p6.get('id_card_raw')}"
    assert p6.get("phone_raw") is None, f"phone_raw pending 时应为 null: {p6.get('phone_raw')}"
    # 衍生字段应已计算
    assert p6.get("age") is not None, "age 应为计算值"
    assert p6.get("pinyin") is not None, "pinyin 应为计算值"

    # 7. 解绑手机号
    r = c.post("/api/user/unbind", json={"type": "phone"}, headers=h)
    check("7. 解绑手机号", r)

print(f"\n{'='*40}  {'🎉 全部通过' if fail==0 else f'{ok}/{ok+fail} 通过'}  {'='*40}")
