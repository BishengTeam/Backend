from __future__ import annotations

import json

from app.services.admin_quiz import AdminQuizService


_CSV_HEADER = (
    "category_path,question_type,question_text,options,correct_answer,explanation"
)


def _csv(question_type: str, answer: str) -> bytes:
    return (
        f"{_CSV_HEADER}\n"
        f'"[""网络基础"",""传输层""]",{question_type},"TCP 三次握手：____、____、ACK","",{answer},"解析"\n'
    ).encode("utf-8")


def test_csv_import_parses_fill_blank_separator_syntax() -> None:
    rows, errors = AdminQuizService()._parse_import_rows(
        "csv",
        _csv("fill_blank", "SYN;;同步|SYN+ACK;;确认应答"),
    )

    assert errors == []
    assert len(rows) == 1
    _, item = rows[0]
    assert item.question_type.value == "fill_blank"
    assert item.correct_answer == [["SYN", "同步"], ["SYN+ACK", "确认应答"]]


def test_csv_import_accepts_chinese_type_aliases() -> None:
    rows, errors = AdminQuizService()._parse_import_rows(
        "csv",
        _csv("填空题", "SYN|SYN+ACK"),
    )

    assert errors == []
    assert rows[0][1].question_type.value == "fill_blank"


def test_csv_import_reports_delimiter_conflicts() -> None:
    rows, errors = AdminQuizService()._parse_import_rows(
        "csv",
        _csv("fill_blank", "SYN;;同步||SYN+ACK"),
    )

    assert rows == []
    assert len(errors) == 1
    assert errors[0]["error_code"] == "answer_delimiter_conflict"
    assert errors[0]["field"] == "correct_answer"


def test_json_import_supports_two_dimensional_candidates_and_alias() -> None:
    payload = {
        "questions": [
            {
                "category_path": ["网络基础", "传输层"],
                "question_type": "填空",
                "question_text": "TCP 三次握手：____、____、ACK",
                "correct_answer": [["SYN", "同步"], ["SYN+ACK"]],
                "explanation": "三次握手建立连接",
            },
            {
                "category_path": ["网络基础", "传输层"],
                "question_type": "问答",
                "question_text": "简述 TCP 与 UDP 的区别。",
                "correct_answer": "TCP 面向连接、可靠；UDP 无连接、轻量。",
                "explanation": "从连接、可靠性、开销三方面作答。",
            },
        ]
    }
    rows, errors = AdminQuizService()._parse_import_rows(
        "json", json.dumps(payload).encode("utf-8")
    )

    assert errors == []
    assert [item.question_type.value for _, item in rows] == [
        "fill_blank",
        "essay",
    ]
    assert rows[0][1].correct_answer == [["SYN", "同步"], ["SYN+ACK"]]
    assert rows[1][1].correct_answer == "TCP 面向连接、可靠；UDP 无连接、轻量。"


def test_import_reports_placeholder_count_mismatch_error_code() -> None:
    payload = {
        "questions": [
            {
                "category_path": ["网络基础"],
                "question_type": "fill_blank",
                "question_text": "只有一个空____",
                "correct_answer": [["答案一"], ["答案二"]],
            }
        ]
    }
    rows, errors = AdminQuizService()._parse_import_rows(
        "json", json.dumps(payload).encode("utf-8")
    )

    assert rows == []
    assert errors[0]["error_code"] == "fill_blank_placeholder_count_mismatch"
