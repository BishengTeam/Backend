"""
题库模块冒烟测试 — 12 步全链路验证

用法:
    # 1. 获取 token
    JWT_SECRET=smoke-minimum-32-chars-key-here! \
      DB_PORT=3306 DB_PASSWORD=bisheng6@6@6 \
      .venv/bin/python -c "
    from app.adapter.security import create_access_token
    import sys, os
    sys.path.insert(0,'.')
    os.environ.setdefault('JWT_SECRET', 'smoke-minimum-32-chars-key-here!')
    from app.adapter.database import async_session_factory
    from app.domain.user.src.index import User
    from sqlalchemy import select
    import asyncio
    async def main():
        async with async_session_factory() as db:
            u = (await db.execute(select(User).where(User.openid=='test_openid_user_001'))).scalar_one()
            print(create_access_token(u.id, u.openid))
    asyncio.run(main())
    "

    # 2. 运行冒烟测试
    TOKEN=<上一步输出的token> .venv/bin/python tests/smoke/test_quiz_smoke.py
"""
import os
import httpx

BASE = "http://127.0.0.1:8000"
TOKEN = os.environ.get("TOKEN", "")

if not TOKEN:
    print("❌ 请设置 TOKEN 环境变量")
    print("   TOKEN=$(获取token的命令) .venv/bin/python tests/smoke/test_quiz_smoke.py")
    exit(1)

ok = fail = 0
h = {"Authorization": f"Bearer {TOKEN}"}

def check(name, resp, extra=None):
    global ok, fail
    if resp.status_code == 200 and (extra is None or extra(resp)):
        ok += 1; print(f"  ✅ {name}")
    else:
        fail += 1
        detail = f" body={resp.text[:80]}" if resp.status_code != 200 else ""
        print(f"  ❌ {name} — status={resp.status_code}{detail}")

with httpx.Client(base_url=BASE, timeout=30) as c:
    print(f"🧪 题库冒烟测试")

    r = c.get("/api/quiz/categories");                          check("1. 分类树", r)
    cats = r.json().get("data", [])
    cid = cats[0].get("children", [{}])[0].get("id") if cats and cats[0].get("children") else (cats[0]["id"] if cats else 1)

    r = c.get(f"/api/quiz/questions?category_id={cid}&page=1&page_size=1"); check("2. 题目列表", r)
    qid = r.json()["data"]["items"][0]["id"] if r.status_code == 200 else 1

    r = c.post("/api/quiz/submit", json={"question_id": qid, "user_answer": "A"}, headers=h); check("3. 提交答题", r)
    r = c.get("/api/quiz/wrong-book", headers=h);                check("4. 错题本", r)
    r = c.post("/api/quiz/collections", json={"question_id": qid}, headers=h); check("5a. 收藏", r)
    r = c.get("/api/quiz/collections", headers=h);               check("5b. 收藏列表", r)
    r = c.post("/api/quiz/checkin", json={"questions_completed":5}, headers=h); check("6. 打卡", r)
    r = c.get("/api/quiz/stats", headers=h);                     check("7. 统计", r)

    r = c.post("/api/quiz/exam/start", json={"question_count":5}, headers=h); check("8a. 开始考试", r)
    eid = r.json()["data"]["exam_id"] if r.status_code == 200 else 0
    answers = [{"question_id":q["id"],"user_answer":"B"} for q in (r.json().get("data",{}).get("questions",[]))]
    r = c.post("/api/quiz/exam/submit", json={"exam_id":eid,"answers":answers,"elapsed_seconds":120}, headers=h); check("8b. 提交考试", r)

    r = c.get("/api/quiz/exam/history", headers=h);              check("9a. 考试记录", r)
    r = c.get(f"/api/quiz/exam/{eid}", headers=h);               check("9b. 考试详情", r)
    r = c.get("/api/quiz/progress", headers=h);                  check("10. 分类进度", r)
    r = c.get("/api/quiz/recent", headers=h);                    check("11. 近期记录", r)
    r = c.get("/api/quiz/exam/current", headers=h);              check("12. 断点续考", r)

print(f"\n{'='*40}  {'🎉 全部通过' if fail==0 else f'{ok}/{ok+fail} 通过'}  {'='*40}")
