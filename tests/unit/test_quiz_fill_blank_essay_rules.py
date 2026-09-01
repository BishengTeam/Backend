from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.community.src.rule.quiz import (
    QuizRuleViolation,
    answer_score_ratio,
    answers_match,
    normalize_question_payload,
    normalize_submitted_answer,
)


def test_fill_blank_normalization_supports_multi_blank_and_candidates() -> None:
    normalized = normalize_question_payload(
        question_type="fill_blank",
        question_text="TCP 三次握手：____、____、ACK",
        correct_answer=[["SYN", "同步"], ["SYN+ACK", "确认应答"]],
        explanation="三次握手建立连接。",
        require_publishable=True,
    )
    assert normalized.options is None
    assert normalized.correct_answer == [["SYN", "同步"], ["SYN+ACK", "确认应答"]]

    deduped = normalize_question_payload(
        question_type="fill_blank",
        question_text="HTTP 默认端口是____",
        correct_answer=[["80", "80"]],
    )
    assert deduped.correct_answer == [["80"]]


def test_fill_blank_normalization_rejects_structural_errors() -> None:
    with pytest.raises(QuizRuleViolation, match="空位"):
        normalize_question_payload(
            question_type="fill_blank",
            question_text="没有空位的题干",
            correct_answer=[["答案"]],
        )

    with pytest.raises(QuizRuleViolation, match="空数必须与题干空位"):
        normalize_question_payload(
            question_type="fill_blank",
            question_text="两空：____ 和 ____",
            correct_answer=[["只有一个"]],
        )

    with pytest.raises(QuizRuleViolation, match="候选答案不能为空白"):
        normalize_question_payload(
            question_type="fill_blank",
            question_text="单空____",
            correct_answer=[["   "]],
        )

    with pytest.raises(QuizRuleViolation, match="二维数组"):
        normalize_question_payload(
            question_type="fill_blank",
            question_text="单空____",
            correct_answer="答案",
        )

    with pytest.raises(QuizRuleViolation, match="候选字符串数组"):
        normalize_question_payload(
            question_type="fill_blank",
            question_text="单空____",
            correct_answer=["答案"],
        )

    with pytest.raises(QuizRuleViolation, match="最多 5 个候选"):
        normalize_question_payload(
            question_type="fill_blank",
            question_text="单空____",
            correct_answer=[["1", "2", "3", "4", "5", "6"]],
        )


def test_fill_blank_and_essay_reject_options_and_option_images() -> None:
    with pytest.raises(QuizRuleViolation, match="不支持选项"):
        normalize_question_payload(
            question_type="fill_blank",
            question_text="单空____",
            options={"A": "甲", "B": "乙"},
        )

    with pytest.raises(QuizRuleViolation, match="不支持选项图片"):
        normalize_question_payload(
            question_type="essay",
            question_text="简述 ARP 协议作用",
            option_image_urls={"A": "https://cdn.example.com/a.png"},
        )


def test_essay_normalization_requires_reference_answer_on_publish() -> None:
    normalized = normalize_question_payload(
        question_type="essay",
        question_text="简述 TCP 与 UDP 的区别。",
        correct_answer="TCP 面向连接、可靠；UDP 无连接、轻量。",
        explanation="从连接、可靠性、开销三方面作答。",
        require_publishable=True,
    )
    assert normalized.options is None
    assert normalized.correct_answer == "TCP 面向连接、可靠；UDP 无连接、轻量。"

    with pytest.raises(QuizRuleViolation, match="参考答案"):
        normalize_question_payload(
            question_type="essay",
            question_text="没有参考答案",
            require_publishable=True,
        )

    with pytest.raises(QuizRuleViolation, match="5000"):
        normalize_question_payload(
            question_type="essay",
            question_text="超长参考答案",
            correct_answer="长" * 5001,
        )


def test_fill_blank_submission_matches_candidates_exactly() -> None:
    correct = [["SYN", "同步"], ["SYN+ACK"]]

    assert answers_match(
        "fill_blank", ["同步", "SYN+ACK"], correct
    )
    assert not answers_match(
        "fill_blank", ["syn", "SYN+ACK"], correct
    )
    assert not answers_match(
        "fill_blank", [" SYN", "SYN+ACK"], correct
    )
    assert not answers_match(
        "fill_blank", ["SYN"], correct
    )

    with pytest.raises(QuizRuleViolation, match="必须为 2 空"):
        normalize_submitted_answer(
            "fill_blank",
            ["只有一个"],
            options=None,
            expected_blank_count=2,
        )


def test_essay_submission_is_text_and_cannot_auto_match() -> None:
    submitted = normalize_submitted_answer(
        "essay", "  TCP 面向连接。  ", options=None
    )
    assert submitted == "TCP 面向连接。"

    with pytest.raises(QuizRuleViolation, match="2000"):
        normalize_submitted_answer("essay", "长" * 2001, options=None)

    with pytest.raises(QuizRuleViolation, match="问答题不支持自动判分"):
        answers_match("essay", "答案", "参考答案")


def test_answer_score_ratio_covers_partial_and_manual_cases() -> None:
    assert answer_score_ratio(
        "fill_blank", ["SYN", "错误"], [["SYN", "同步"], ["SYN+ACK"]]
    ) == Decimal("0.5")

    assert answer_score_ratio(
        "fill_blank", ["SYN", "SYN+ACK"], [["SYN", "同步"], ["SYN+ACK"]]
    ) == Decimal(1)

    assert answer_score_ratio(
        "fill_blank", ["错", "错"], [["SYN"], ["SYN+ACK"]]
    ) == Decimal(0)

    assert answer_score_ratio(
        "single_choice", "A", "A", options={"A": "甲", "B": "乙"}
    ) == Decimal(1)

    assert answer_score_ratio("essay", "学生答案", "参考答案") is None
