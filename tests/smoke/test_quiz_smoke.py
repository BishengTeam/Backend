#!/usr/bin/env python3
"""题库模块 冒烟测试（HTTP 版）

用法:
  先获取 token:
    cd /home/bisheng/work/weMiniApp/Backend
    USER_TOKEN=$(PYTHONPATH=. .venv/bin/python3 .temp/gen_token2.py 2>/dev/null | grep '^eyJ')
    echo $USER_TOKEN

  再跑测试:
    USER_TOKEN=<user_token> .venv/bin/python3 tests/smoke/test_quiz_smoke.py

前置条件: 服务运行在 http://127.0.0.1:8000，已执行 seed_testdata
"""

import os
import json
import sys
import httpx

BASE = "http://127.0.0.1:8000"
USER_TOKEN = os.getenv("USER_TOKEN", "")

pass_count = 0
fail_count = 0
question_id_single: int | None = None
question_id_multi: int | None = None
question_id_judge: int | None = None
wrong_book_id: int | None = None
collection_id: int | None = None
exam_id: int | None = None


def _h() -> dict[str, str]:
    return {"Authorization": f"Bearer {USER_TOKEN}", "Content-Type": "application/json"}


def _get(path: str, **params):
    return httpx.get(f"{BASE}{path}", headers=_h(), params=params, timeout=15)


def _post(path: str, body: dict):
    return httpx.post(f"{BASE}{path}", json=body, headers=_h(), timeout=15)


def _delete(path: str):
    return httpx.delete(f"{BASE}{path}", headers=_h(), timeout=15)


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
            print(f"  body: {json.dumps(body, ensure_ascii=False, indent=2)[:800]}")
        except Exception:
            print(f"  body: {resp.text[:800]}")


def main():
    global pass_count, fail_count
    global question_id_single, question_id_multi, question_id_judge
    global wrong_book_id, collection_id, exam_id

    if not USER_TOKEN:
        print("❌ 请设置 USER_TOKEN 环境变量")
        sys.exit(1)

    # ═══════════════════════════════════════════════════════════════
    # 第一阶段: 题库浏览（无需登录）
    # ═══════════════════════════════════════════════════════════════

    print("\n" + "=" * 60)
    print("  第一阶段: 题库浏览")
    print("=" * 60)

    expect("0.1 获取分类树",
           httpx.get(f"{BASE}/api/quiz/categories", timeout=15),
           checks=[("code", "0")])

    expect("0.2 获取题目列表（不限分类，第一页）",
           httpx.get(f"{BASE}/api/quiz/questions", params={"page": 1, "page_size": 5}, timeout=15),
           checks=[("code", "0")])

    # ═══════════════════════════════════════════════════════════════
    # 第二阶段: 题目数据准备（获取各题型的题目 ID）
    # ═══════════════════════════════════════════════════════════════

    print("\n" + "=" * 60)
    print("  第二阶段: 题目数据准备")
    print("=" * 60)

    resp = _get("/api/quiz/questions", page=1, page_size=50)
    if resp.status_code == 200:
        items = resp.json().get("data", {}).get("items", [])
        for q in items:
            qtype = q.get("question_type", "")
            if question_id_single is None and qtype == "single_choice":
                question_id_single = q["id"]
            elif question_id_multi is None and qtype == "multiple_choice":
                question_id_multi = q["id"]
            elif question_id_judge is None and qtype == "judge":
                question_id_judge = q["id"]

    print(f"  单选题 id={question_id_single}")
    print(f"  多选题 id={question_id_multi}")
    print(f"  判断题 id={question_id_judge}")

    # ═══════════════════════════════════════════════════════════════
    # 第三阶段: 答题 — 单选（正确）
    # ═══════════════════════════════════════════════════════════════

    print("\n" + "=" * 60)
    print("  第三阶段: 答题 — 单选（正确）")
    print("=" * 60)

    # seed data: OSPF 协议 -> 正确答案 B, BGP -> B, DDoS -> B, iptables -> B
    # 列表接口不返回 correct_answer，直接使用预期值
    correct_answer = "B"
    resp_single = _get("/api/quiz/questions", question_type="single_choice", page=1, page_size=1)
    if resp_single.status_code == 200:
        items = resp_single.json().get("data", {}).get("items", [])
        if items:
            question_id_single = items[0]["id"]
            print(f"  题目: {items[0].get('question_text', '')[:30]}...")
            print(f"  预期正确: {correct_answer}")

    expect("3.1 提交正确答案",
           _post("/api/quiz/submit", {"question_id": question_id_single, "user_answer": correct_answer}),
           checks=[("code", "0"), ("data.is_correct", "True")])

    # ═══════════════════════════════════════════════════════════════
    # 第四阶段: 答题 — 单选（错误） + 加入错题本 + 收藏
    # ═══════════════════════════════════════════════════════════════

    print("\n" + "=" * 60)
    print("  第四阶段: 答题错误 → 错题本 + 收藏")
    print("=" * 60)

    wrong_answer = "X"  # 故意答错
    resp_wrong = _post("/api/quiz/submit", {"question_id": question_id_single, "user_answer": wrong_answer})

    expect("4.1 提交错误答案",
           resp_wrong,
           checks=[("code", "0"), ("data.is_correct", "False")])

    # 加入错题本
    expect("4.2 加入错题本",
           _post("/api/quiz/wrong-book", {"question_id": question_id_single}),
           checks=[("code", "0")])

    # 收藏
    resp_col = _post("/api/quiz/collections", {"question_id": question_id_single})
    if resp_col.status_code == 200 and resp_col.json().get("code") == "0":
        collection_id = resp_col.json()["data"]["id"]

    expect("4.3 收藏题目",
           resp_col,
           checks=[("code", "0")])

    # ═══════════════════════════════════════════════════════════════
    # 第五阶段: 答题 — 多选 + 判断
    # ═══════════════════════════════════════════════════════════════

    print("\n" + "=" * 60)
    print("  第五阶段: 多选 + 判断 答题")
    print("=" * 60)

    # 多选
    resp_multi = _get("/api/quiz/questions", question_type="multiple_choice", page=1, page_size=1)
    multi_answer = None
    multi_text = ""
    if resp_multi.status_code == 200:
        items = resp_multi.json().get("data", {}).get("items", [])
        if items:
            multi_answer = items[0].get("correct_answer")
            question_id_multi = items[0]["id"]
            multi_text = items[0].get("question_text", "")[:30]

    if multi_answer:
        print(f"  多选题目: {multi_text}")
        expect("5.1 多选题正确",
               _post("/api/quiz/submit", {"question_id": question_id_multi, "user_answer": multi_answer}),
               checks=[("code", "0"), ("data.is_correct", "True")])

    # 判断
    resp_judge = _get("/api/quiz/questions", question_type="judge", page=1, page_size=1)
    judge_answer = None
    if resp_judge.status_code == 200:
        items = resp_judge.json().get("data", {}).get("items", [])
        if items:
            judge_answer = items[0].get("correct_answer")
            question_id_judge = items[0]["id"]

    if judge_answer:
        print(f"  判断题目: {items[0].get('question_text', '')[:30]}")
        expect("5.2 判断题正确",
               _post("/api/quiz/submit", {"question_id": question_id_judge, "user_answer": judge_answer}),
               checks=[("code", "0"), ("data.is_correct", "True")])

    # ═══════════════════════════════════════════════════════════════
    # 第六阶段: 错题本查询
    # ═══════════════════════════════════════════════════════════════

    print("\n" + "=" * 60)
    print("  第六阶段: 错题本")
    print("=" * 60)

    resp_wb = _get("/api/quiz/wrong-book", page=1, page_size=10)
    expect("6.1 错题本列表（应有内容）",
           resp_wb,
           checks=[("code", "0")])

    # 获取错题本第一条的 ID
    if resp_wb.status_code == 200:
        items = resp_wb.json().get("data", {}).get("items", [])
        if items:
            wrong_book_id = items[0]["id"]

    # ═══════════════════════════════════════════════════════════════
    # 第七阶段: 收藏查询 + 取消收藏
    # ═══════════════════════════════════════════════════════════════

    print("\n" + "=" * 60)
    print("  第七阶段: 收藏")
    print("=" * 60)

    expect("7.1 收藏列表（应有内容）",
           _get("/api/quiz/collections", page=1, page_size=10),
           checks=[("code", "0")])

    # ═══════════════════════════════════════════════════════════════
    # 第八阶段: 签到
    # ═══════════════════════════════════════════════════════════════

    print("\n" + "=" * 60)
    print("  第八阶段: 签到")
    print("=" * 60)

    expect("8.1 签到状态",
           _get("/api/quiz/checkin"),
           checks=[("code", "0")])

    from datetime import date
    expect("8.2 执行签到",
           _post("/api/quiz/checkin", {"date": str(date.today())}),
           checks=[("code", "0")])

    # ═══════════════════════════════════════════════════════════════
    # 第九阶段: 练习统计 + 进度 + 近期记录
    # ═══════════════════════════════════════════════════════════════

    print("\n" + "=" * 60)
    print("  第九阶段: 统计 + 进度 + 近期")
    print("=" * 60)

    expect("9.1 练习统计",
           _get("/api/quiz/stats"),
           checks=[("code", "0")])

    expect("9.2 分类进度",
           _get("/api/quiz/progress"),
           checks=[("code", "0")])

    expect("9.3 近期答题记录",
           _get("/api/quiz/recent", limit=5),
           checks=[("code", "0")])

    # ═══════════════════════════════════════════════════════════════
    # 第十阶段: 模拟考试
    # ═══════════════════════════════════════════════════════════════

    print("\n" + "=" * 60)
    print("  第十阶段: 模拟考试")
    print("=" * 60)

    resp_exam_start = _post("/api/quiz/exam/start", {
        "question_count": 3,
        "duration_minutes": 5,
    })
    expect("10.1 开始考试",
           resp_exam_start,
           checks=[("code", "0")])

    if resp_exam_start.status_code == 200:
        data = resp_exam_start.json().get("data", {})
        exam_id = data.get("id") or data.get("exam_id")

    # 断点续考
    expect("10.2 断点续考（当前考试）",
           _get("/api/quiz/exam/current"),
           checks=[("code", "0")])

    # 获取考试题目，逐题提交
    if exam_id:
        resp_exam_detail = _get(f"/api/quiz/exam/{exam_id}")
        if resp_exam_detail.status_code == 200:
            exam_data = resp_exam_detail.json().get("data", {})
            questions = exam_data.get("questions", [])
            if questions:
                q = questions[0]
                resp_ans = _post("/api/quiz/exam/submit", {
                    "exam_id": exam_id,
                    "question_id": q.get("question_id") or q.get("id"),
                    "user_answer": q.get("correct_answer", "A"),
                })
                expect("10.3 提交考试答案",
                       resp_ans,
                       checks=[("code", "0")])

    expect("10.4 考试记录",
           _get("/api/quiz/exam/history", page=1, page_size=10),
           checks=[("code", "0")])

    # ═══════════════════════════════════════════════════════════════
    # 清理: 删除错题 + 取消收藏
    # ═══════════════════════════════════════════════════════════════

    print("\n" + "=" * 60)
    print("  清理: 删除测试数据")
    print("=" * 60)

    if wrong_book_id:
        expect("C.1 移除错题",
               _delete(f"/api/quiz/wrong-book/{wrong_book_id}"),
               checks=[("code", "0")])

    if collection_id:
        expect("C.2 取消收藏",
               _delete(f"/api/quiz/collections/{collection_id}"),
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
